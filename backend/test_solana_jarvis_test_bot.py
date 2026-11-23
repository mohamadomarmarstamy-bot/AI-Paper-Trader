from contextlib import closing
from pathlib import Path
import json
import math
import tempfile
from threading import Barrier
import solana_jupiter
import unittest
from unittest.mock import patch

import database
import jarvis_test_bot as jarvis

MINT = '11111111111111111111111111111112'
PAIR = '11111111111111111111111111111113'


class Provider:
    def __init__(self, clock):
        self.clock = clock
        self.price = 1

    def profiles(self):
        return [MINT]

    def pairs(self, addresses):
        return [{
            'chainId': 'solana', 'pairAddress': PAIR, 'baseToken': {'address': MINT, 'name': 'Example'},
            'priceUsd': self.price, 'pairCreatedAt': (self.clock() - 7200) * 1000,
            'liquidity': {'usd': 150_000}, 'volume': {'m5': 20_000},
            'txns': {'m5': {'buys': 50, 'sells': 30}}, 'priceChange': {'m5': 2},
        }]


class JarvisTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(directory) / 'original.db'))
        self.now = 1795110000
        self.market = Provider(lambda: self.now)
        self.sell = 45_000_000

    def advance(self, seconds=5):
        self.now += seconds
        jarvis.tick(providers=self.market, get_quote=self.quote, clock=lambda: self.now)

    def quote(self, input_mint, output_mint, amount):
        return 1_000_000 if input_mint == jarvis.USDC else self.sell

    def state(self):
        with jarvis.connection() as conn:
            return jarvis.read(conn)[0]

    def test_step_floor_and_quote_exit_are_isolated(self):
        self.assertEqual([jarvis.exit_floor(x) for x in (0, 9.99, 10, 20, 29.99, 30)],
                         [-10, -10, 0, 10, 10, 20])
        jarvis.set_enabled(True)
        self.advance()
        state = self.state()
        self.assertIn(MINT, state['bots']['instant-step']['positions'])
        self.assertIn(MINT, state['bots']['instant-trim']['positions'])
        self.assertFalse(state['bots']['patient-step']['positions'])
        self.assertFalse(state['bots']['jarvis']['positions'])
        self.assertIsNone(state['strategy'])
        self.assertEqual(state['bots']['jarvis']['cash'], jarvis.JARVIS_BUDGET)

        jarvis.set_enabled(False)
        self.advance()
        report = jarvis.history(False)
        self.assertEqual(report['coins'][0]['mint'], MINT)
        self.assertAlmostEqual(report['coins'][0]['trades'][0]['pnl'], -5.35)
        self.assertEqual(report['coins'][0]['trades'][0]['held_seconds'], 5)
        self.assertFalse(self.state()['bots']['instant-step']['positions'])
        self.assertFalse(self.state()['bots']['instant-trim']['positions'])
        self.assertEqual(len(report['coins'][0]['trades']), 6)
        self.assertFalse(jarvis.history(True)['coins'])
        self.assertFalse(database.DATABASE_PATH.exists())

    def test_estimated_totals_keep_last_sell_quote_and_value_unquoted_holdings_at_cost(self):
        jarvis.set_enabled(True)
        self.advance()
        self.sell = 50_000_000
        self.advance()
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            quoted = state['bots']['instant-step']['positions'][MINT]
            self.assertEqual(quoted['mark'], 49_900_000)
            jarvis.open_trade(state, 'instant-step', {
                'mint': 'new-holding', 'symbol': 'Unquoted', 'liquidity_usd': 120_000, 'age_minutes': 300,
            }, 10, self.now)
            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(state),))

        def missing_quote(*_args):
            raise ValueError('No sell route')

        self.now += jarvis.ROUTINE_EXIT_INTERVAL + 1
        jarvis.tick(providers=self.market, get_quote=missing_quote, clock=lambda: self.now)
        self.now += jarvis.POLL
        jarvis.tick(providers=self.market, get_quote=missing_quote, clock=lambda: self.now)
        with patch.object(jarvis.time, 'time', return_value=self.now):
            bot = jarvis.status()['bots'][0]
        self.assertIsNone(bot['balance'])
        self.assertAlmostEqual(bot['estimated_balance'], 100.15)
        self.assertAlmostEqual(bot['estimated_pnl'], -.35)
        self.assertEqual(bot['valuation'], {'at_cost': 1, 'last_quote': 1})
        self.assertEqual([item['valuation_basis'] for item in bot['holdings']], ['last_quote', 'cost'])

    def test_quote_capacity_reserves_exits_and_deferral_does_not_spend_a_slot(self):
        with jarvis.connection() as conn:
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 60)
            for mint in ('a', 'b', 'c'):
                jarvis.open_trade(state, 'instant-step', {
                    'mint': mint, 'symbol': mint, 'liquidity_usd': 1, 'age_minutes': 1,
                }, 10, self.now - 20)
            state['watch']['candidate'] = {'mint': 'candidate', 'symbol': 'Candidate',
                                           'first': self.now, 'last': self.now, 'pullback': None,
                                           'liquidity_usd': 1, 'age_minutes': 1}
            calls = []

            def quote(input_mint, output_mint, _amount):
                calls.append((input_mint, output_mint))
                if len(calls) == 1:
                    raise jarvis.Deferred('Quote slot reserved upstream')
                return 10 if input_mint == jarvis.USDC else 50_000_000

            for offset in range(0, 8, 2):
                jarvis.step(conn, state, self.market, quote, self.now + offset)

            self.assertEqual([source for source, _ in calls], ['a', 'a', 'b', jarvis.USDC])
            self.assertIn('candidate', state['bots']['instant-step']['positions'])
            self.assertIn('candidate', state['bots']['instant-trim']['positions'])

    def test_failed_routes_keep_retrying_without_blocking_fresh_quotes(self):
        state = jarvis.initial()
        portfolio = state['bots']['instant-step']
        for mint in ('retry', 'fresh'):
            jarvis.open_trade(state, 'instant-step', {
                'mint': mint, 'symbol': mint, 'liquidity_usd': 1, 'age_minutes': 1,
            }, 10, self.now - 10)
        retry = portfolio['positions']['retry']
        retry.update(mark_stale=True, quote_due_since=self.now - 3600, route_failures=3)
        jobs = jarvis.quote_jobs(state, self.now)
        pressure = jarvis.exit_queue(jobs, self.now)
        self.assertEqual(pressure['retry_due'], 1)
        self.assertFalse(pressure['backed_up'])
        self.assertEqual(jarvis.next_exit(jobs, 0), ('fresh', 10))
        self.assertEqual(jarvis.next_exit(jobs, 5), ('retry', 10))

        def unavailable(*args):
            raise jarvis.QuoteUnavailable('Jupiter returned no route')

        state['exit_quote_attempts'] = 5
        with jarvis.connection() as conn:
            jarvis.step(conn, state, self.market, unavailable, self.now)
            self.assertEqual(retry['due'], self.now + 360)
            self.assertEqual(retry['route_failures'], 4)
            self.assertEqual(retry['quote_due_since'], self.now - 3600)
            state['exit_quote_attempts'] = 11
            jarvis.step(conn, state, self.market, lambda *args: 50_000_000, retry['due'])
        self.assertNotIn('route_failures', retry)
        self.assertNotIn('quote_due_since', retry)
        self.assertFalse(retry['mark_stale'])

    def test_temporary_exit_failure_retries_quickly_without_changing_accounting(self):
        state = jarvis.initial()
        jarvis.open_trade(state, 'instant-step', {
            'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 1, 'age_minutes': 1,
        }, 10, self.now - 10)
        portfolio = state['bots']['instant-step']
        position = portfolio['positions'][MINT]
        cash, cost = portfolio['cash'], position['cost']

        def unavailable(*args):
            raise jarvis.QuoteUnavailable('Jupiter temporarily unavailable.', 'temporary')

        with jarvis.connection() as conn:
            jarvis.step(conn, state, self.market, unavailable, self.now)
            self.assertEqual(position['due'], self.now + 6)
            self.assertEqual(position['route_failure']['category'], 'temporary')
            self.assertEqual((portfolio['cash'], position['cost'], position['quantity']), (cash, cost, 10))
            jarvis.step(conn, state, self.market, lambda *args: 50_000_000, self.now + 6)
        self.assertNotIn('route_failure', position)
        self.assertNotIn('route_failures', position)
        self.assertFalse(position['mark_stale'])
        self.assertEqual(state['last_exit_quote_failure']['mint'], MINT)
        self.assertEqual(state['last_exit_quote_failure']['amount'], 10)

    def test_temporary_pause_drains_exits_and_resumes_only_after_sustained_health(self):
        state = jarvis.initial()
        state.update(enabled=True, next_discovery=self.now + 1000)
        p = self.monitored_position(state)
        p['due'] = self.now - 200
        with patch.dict('os.environ', {'JARVIS_ENTRY_CATCHUP_REQUEST': 'test-request'}):
            jobs = jarvis.quote_jobs(state, self.now)
            self.assertTrue(jarvis.update_catchup_pause(state, jobs, self.now))
            self.assertTrue(jarvis.catchup_requested(state))
            # Backlog does not count toward the healthy interval.
            self.assertIsNone(state['entry_catchup_pause']['healthy_since'])
            self.assertTrue(jarvis.update_catchup_pause(state, {}, self.now + 10))
            self.assertTrue(jarvis.update_catchup_pause(state, {}, self.now + 39))
            self.assertFalse(jarvis.update_catchup_pause(state, {}, self.now + 40))
            self.assertEqual(state['entry_catchup_pause']['resumed_at'], self.now + 40)
            # Restarts with the same request do not arm the one-time pause again.
            restored = json.loads(json.dumps(state))
            self.assertFalse(jarvis.update_catchup_pause(restored, jobs, self.now + 50))
            self.assertFalse(jarvis.catchup_requested(restored))
        self.assertTrue(state['enabled'])

    def test_final_close_commits_early_resume_and_email_with_all_closed_positions(self):
        import jarvis_backlog as guard
        jarvis.set_enabled(True)
        self.advance()
        with jarvis.connection() as conn, conn:
            state, revision = jarvis.read(conn)
            count = sum(len(b['positions']) for b in state['bots'].values())
            self.assertGreater(count, 0)
            guard.apply_control(state, 30, self.now)
            guard.record_pause(conn, state, 30, self.now)
            guard.save_state(conn, state, revision)
        self.advance()
        state = self.state()
        self.assertTrue(state['enabled'])
        self.assertFalse(any(b['positions'] for b in state['bots'].values()))
        self.assertEqual(state['entry_pause_until'], 0)
        self.assertTrue(state['entry_catchup_pause']['early_all_closed'])
        with jarvis.connection() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM backlog_mail WHERE id LIKE 'resume/%'").fetchone()[0])
            self.assertIn(f'{count} Jarvis paper positions closed during this timer.', payload['text'])

    def test_timed_buy_pause_blocks_entries_without_stopping_exits(self):
        import jarvis_backlog as guard
        with jarvis.connection() as conn:
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 1000, exit_attempts_since_entry=6)
            p = self.monitored_position(state)
            p['due'] = self.now - 20
            state['watch']['candidate'] = dict(state['watch'][MINT], mint='candidate')
            guard.apply_control(state, 10, self.now)
            calls = []
            def quote(*args):
                calls.append(args)
                return 50000000
            jarvis.step(conn, state, None, quote, self.now)
            self.assertEqual(calls, [(MINT, jarvis.USDC, 100)])
            jarvis.step(conn, state, None, quote, self.now + 1)
            self.assertEqual(len(calls), 1)
            self.assertTrue(state['enabled'])

    def test_pause_keeps_sells_running_and_blocks_buys(self):
        with patch.dict('os.environ', {'JARVIS_ENTRY_CATCHUP_REQUEST': 'pause'}), jarvis.connection() as conn:
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 1000, exit_attempts_since_entry=6)
            p = self.monitored_position(state)
            p['due'] = self.now - 20
            state['watch']['candidate'] = dict(state['watch'][MINT], mint='candidate')
            calls = []
            def quote(*args):
                calls.append(args)
                return 50000000
            jarvis.step(conn, state, None, quote, self.now)
            self.assertEqual(calls, [(MINT, jarvis.USDC, 100)])
            jarvis.step(conn, state, None, quote, self.now + 1)
            self.assertEqual(len(calls), 1)
            self.assertNotIn('candidate', state['bots']['instant-step']['positions'])
            self.assertTrue(state['entry_catchup_pause']['active'])

    def test_pause_health_resets_and_retry_only_does_not_prevent_recovery(self):
        state = jarvis.initial()
        p = self.monitored_position(state)
        p.update(due=self.now - 100, quote_due_since=self.now - 100, mark_stale=True)
        with patch.dict('os.environ', {'JARVIS_ENTRY_CATCHUP_REQUEST': 'pause'}):
            retries = jarvis.quote_jobs(state, self.now)
            self.assertTrue(jarvis.update_catchup_pause(state, retries, self.now))
            p['mark_stale'] = False
            self.assertTrue(jarvis.update_catchup_pause(state, jarvis.quote_jobs(state, self.now + 20), self.now + 20))
            self.assertIsNone(state['entry_catchup_pause']['healthy_since'])
            self.assertTrue(jarvis.update_catchup_pause(state, {}, self.now + 21))
            self.assertFalse(jarvis.update_catchup_pause(state, {}, self.now + 51))

    def test_exit_comparison_separates_closed_and_open_cohorts_without_missing_metrics_as_zero(self):
        cutoff = jarvis.EXIT_SCHEDULER_STARTED
        with jarvis.connection() as conn, conn:
            def trade(mint, opened, closed, wait=None, gap=None):
                data = {'exit_quote_wait': wait, 'stop_gap_points': gap}
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES(?,?,?,?,?,?,?,?)',
                             ('instant-step', mint, opened, closed, 'test', 0, jarvis.BUY_COST, json.dumps(data)))
            trade('excluded', cutoff - 4000, cutoff - 1801, 999, 99)
            trade('before', cutoff - 2000, cutoff - 1, 120, 14)
            trade('carried', cutoff - 100, cutoff + 5, 90, 8)
            trade('new', cutoff, cutoff + 10, 2, 1)
            trade('missing', cutoff + 1, cutoff + 20)
            trade('future', cutoff + 1, cutoff + 100, 999, 99)
            state = jarvis.initial()
            old = self.monitored_position(state)
            old.update(opened=cutoff - 1, mark_stale=True, quote_due_since=cutoff - 30)
            new = self.monitored_position(state, mint='open-new')
            new['opened'] = cutoff
            result = jarvis.exit_comparison(conn, state, cutoff + 30)
            before, carried, fresh = result['cohorts']
            self.assertEqual(before['closed'], 1)
            self.assertEqual(before['late_stop_gap'], 14)
            self.assertEqual(carried['late_stops'], 1)
            self.assertEqual(carried['open'], 1)
            self.assertEqual(carried['route_retries'], 1)
            self.assertEqual(fresh['closed'], 2)
            self.assertEqual(fresh['quote_samples'], 1)
            self.assertEqual(fresh['mean_wait'], 2)
            self.assertEqual(fresh['p95_wait'], 2)
            self.assertEqual(fresh['mean_stop_gap'], 1)
            self.assertEqual(fresh['late_stops'], 0)
            self.assertEqual(fresh['open'], 1)

    def test_empty_exit_comparison_has_unknown_averages(self):
        with jarvis.connection() as conn:
            result = jarvis.exit_comparison(conn, jarvis.initial(), jarvis.EXIT_SCHEDULER_STARTED + 10)
        for cohort in result['cohorts']:
            self.assertEqual(cohort['closed'], 0)
            self.assertIsNone(cohort['mean_wait'])
            self.assertIsNone(cohort['mean_stop_gap'])
            self.assertIsNone(cohort['p95_wait'])

    def monitored_position(self, state, bot='instant-step', mint=MINT):
        row = {'mint': mint, 'pair': PAIR, 'symbol': mint, 'liquidity_usd': 100000,
               'age_minutes': 120, 'price_usd': 1, 'last': self.now, 'first': self.now,
               'pullback': None, 'dip': None, 'high': 1, 'buys_m5': 50, 'sells_m5': 30, 'volume_m5_usd': 20000}
        state['watch'][mint] = row
        jarvis.open_trade(state, bot, row, 100, self.now - 10)
        p = state['bots'][bot]['positions'][mint]
        p.update(mark=49900000, mark_at=self.now - 5, due=self.now + 55,
                 market_anchor={'price': 1, 'amount': 50000000, 'quantity': 100, 'pair': PAIR})
        return p

    def test_batch_monitor_rotates_held_coins_without_profiles_or_trading_activity(self):
        state = jarvis.initial()
        for i in range(205):
            self.monitored_position(state, mint=f'held-{i:03}')
        batches = []
        class HeldProvider:
            def profiles(self):
                return []
            def pairs(inner, addresses):
                batches.extend(addresses)
                result = []
                for mint in addresses:
                    if mint == jarvis.lab.SOL:
                        continue
                    pair = self.market.pairs([MINT])[0]
                    pair['baseToken']['address'] = mint
                    pair['txns']['m5'] = {'buys': 0, 'sells': 0}
                    pair['volume']['m5'] = 0
                    result.append(pair)
                return result
        for offset in (0, 10, 20):
            jarvis.observe(state, HeldProvider(), self.now + offset)
        self.assertTrue(set(state['bots']['instant-step']['positions']) <= set(batches))
        self.assertEqual(state['watch']['held-204']['last'], self.now + 20)
        self.assertEqual(len(batches), 303)

    def test_indicative_stop_and_profit_advance_quotes_without_marking_or_selling(self):
        state = jarvis.initial()
        stop = self.monitored_position(state)
        profit = self.monitored_position(state, 'lab-tp5-sl10', 'profit')
        state['watch'][MINT]['price_usd'] = .85
        state['watch']['profit']['price_usd'] = 1.06
        jarvis.schedule_market_exits(state, self.now)
        for p in (stop, profit):
            self.assertEqual(p['exit_priority'], 0)
            self.assertEqual(p['due'], self.now)
            self.assertEqual(p['quantity'], 100)
            self.assertEqual(p['mark'], 49900000)
        # Indicative prices cannot invent a profit: only the exact quote decides.
        with jarvis.connection() as conn:
            self.assertEqual(jarvis.step(conn, state, None, lambda *_: 50000000, self.now), [])
        self.assertIn('profit', state['bots']['lab-tp5-sl10']['positions'])

    def test_monitor_ignores_stale_other_pool_and_quantity_mismatch(self):
        for change in ('stale', 'pool', 'quantity'):
            state = jarvis.initial()
            p = self.monitored_position(state)
            row = state['watch'][MINT]
            row['price_usd'] = .5
            if change == 'stale':
                row['last'] = self.now - 91
            elif change == 'pool':
                row['pair'] = 'other'
            else:
                p['market_anchor']['quantity'] = 50
            jarvis.schedule_market_exits(state, self.now)
            self.assertEqual(p['exit_priority'], 3)
            self.assertGreater(p['due'], self.now)

    def test_monitor_preserves_route_retry_cooldown_and_prioritizes_time_exit(self):
        state = jarvis.initial()
        retry = self.monitored_position(state)
        retry.update(mark_stale=True, quote_due_since=self.now - 60)
        state['watch'][MINT]['price_usd'] = .5
        timed = self.monitored_position(state, 'lab-five-minute', 'timed')
        timed['opened'] = self.now - 301
        jarvis.schedule_market_exits(state, self.now)
        self.assertEqual(retry['due'], self.now + 55)
        self.assertEqual(timed['due'], self.now)
        self.assertEqual(timed['exit_priority'], 0)

    def test_urgent_exit_preempts_buy_and_safe_positions_poll_less_often(self):
        with jarvis.connection() as conn:
            state = jarvis.initial()
            p = self.monitored_position(state, 'lab-tp5-sl10')
            state.update(enabled=True, next_discovery=self.now + 60, exit_attempts_since_entry=6,
                         exit_quote_attempts=1)
            state['watch'][MINT]['price_usd'] = 1.06
            candidate = dict(state['watch'][MINT], mint='candidate', pullback=None)
            state['watch']['candidate'] = candidate
            calls = []
            def quote(*args):
                calls.append(args)
                return 49500000
            jarvis.step(conn, state, None, quote, self.now)
            self.assertEqual(calls, [(MINT, jarvis.USDC, 100)])
            self.assertEqual(p['due'], self.now + jarvis.ROUTINE_EXIT_INTERVAL)
            self.assertEqual(p['mark'], 49400000)

    def test_exit_priority_reserves_every_third_attempt_for_oldest(self):
        state = jarvis.initial()
        portfolio = state['bots']['instant-step']
        for mint, delay, mark in [('old', 30, None), ('near', 10, 47_000_000), ('below', 3, 44_000_000)]:
            jarvis.open_trade(state, 'instant-step', {
                'mint': mint, 'symbol': mint, 'liquidity_usd': 1, 'age_minutes': 1,
            }, 10, self.now - delay)
            portfolio['positions'][mint]['mark'] = mark

        jobs = jarvis.quote_jobs(state, self.now)
        self.assertEqual(jarvis.next_exit(jobs, 1), ('below', 10))
        del jobs[('below', 10)]
        self.assertEqual(jarvis.next_exit(jobs, 2), ('near', 10))
        self.assertEqual(jarvis.next_exit(jobs, 3), ('old', 10))

    def test_exit_backlog_uses_six_quote_attempts_before_entry(self):
        with jarvis.connection() as conn:
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 60)
            for index in range(7):
                mint = f'exit-{index}'
                jarvis.open_trade(state, 'instant-step', {
                    'mint': mint, 'symbol': mint, 'liquidity_usd': 1, 'age_minutes': 1,
                }, 10, self.now - 100)
            state['watch']['candidate'] = {'mint': 'candidate', 'symbol': 'Candidate',
                                           'first': self.now, 'last': self.now, 'pullback': None,
                                           'liquidity_usd': 1, 'age_minutes': 1}
            calls = []

            def quote(input_mint, _output_mint, _amount):
                calls.append(input_mint)
                return 10 if input_mint == jarvis.USDC else 45_000_000

            for offset in range(0, 14, 2):
                jarvis.step(conn, state, self.market, quote, self.now + offset)

        self.assertEqual(calls, [f'exit-{index}' for index in range(6)] + [jarvis.USDC])
        self.assertIn('exit-6', state['bots']['instant-step']['positions'])
        self.assertIn('candidate', state['bots']['instant-step']['positions'])

    def test_health_reports_new_cohort_without_mixing_prior_trades(self):
        jarvis.set_enabled(True)
        self.advance()
        jarvis.set_enabled(False)
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            del state['bots']['instant-step']['positions'][MINT]['exit_cohort']
            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(state),))

        self.advance()
        with patch.object(jarvis.time, 'time', return_value=self.now):
            ladder, trim = jarvis.status()['bots'][:2]

        self.assertEqual(ladder['closed'], 1)
        self.assertEqual(ladder['exit_sample']['closed'], 0)
        sample = trim['exit_sample']
        self.assertEqual(sample['closed'], 1)
        self.assertAlmostEqual(sample['pnl'], -5.35)
        self.assertEqual(sample['quote_wait'], 3)
        self.assertEqual(sample['stop_exits'], 1)
        self.assertAlmostEqual(sample['stop_gap'], 100 * (1 - 44_900_000 / jarvis.BUY_COST) - 10)

    def test_discovery_rotates_full_coin_batches_and_keeps_sol_separate(self):
        mints = [f'mint-{i}' for i in range(240)]
        batches = []

        class BatchProvider:
            def profiles(self):
                return mints

            def pairs(self, addresses):
                batches.append(addresses)
                return []

        state = jarvis.initial()
        jarvis.observe(state, BatchProvider(), self.now)
        jarvis.observe(state, BatchProvider(), self.now + 20)
        self.assertEqual(sorted(map(len, batches)), [1, 1] + [20] * 10)
        self.assertEqual(sum(jarvis.lab.SOL in batch for batch in batches), 2)
        batches = [batch for batch in batches if jarvis.lab.SOL not in batch]
        ordered = sorted(batches, key=lambda batch: int(batch[0].split('-')[1]))
        self.assertEqual([mint for batch in ordered for mint in batch], mints[:200])

    def test_discovery_overlaps_batches_and_retains_partial_results(self):
        barrier = Barrier(5)
        market = self.market

        class ParallelProvider:
            def profiles(self):
                return [MINT] + [f'mint-{i}' for i in range(99)]

            def pairs(self, addresses):
                if addresses == [jarvis.lab.SOL]:
                    return []
                barrier.wait(timeout=5)
                if MINT in addresses:
                    return market.pairs([MINT])
                raise ValueError('One source batch unavailable')

        state = jarvis.initial()
        jarvis.observe(state, ParallelProvider(), self.now)
        self.assertIn(MINT, state['watch'])
        self.assertEqual(state['error'], 'Some discovery data is unavailable.')

    def test_faster_cadence_uses_more_quotes_without_exceeding_shared_budget(self):
        def run(interval):
            state = jarvis.initial()
            state.update(enabled=True, next_discovery=self.now + 1000)
            for i in range(100):
                state['watch'][f'candidate-{i}'] = {
                    'mint': f'candidate-{i}', 'symbol': 'Candidate',
                    'first': self.now, 'last': self.now, 'pullback': self.now,
                    'liquidity_usd': 1, 'age_minutes': 1,
                }
            budget = solana_jupiter.Budget()
            clock = [self.now]
            starts = []

            def quote(source, target, amount):
                with budget.permit(research=True):
                    starts.append(clock[0])
                    return 10 if source == jarvis.USDC else 45_000_000

            with jarvis.connection() as conn, patch.object(solana_jupiter.time, 'monotonic', side_effect=lambda: clock[0]), patch.dict('os.environ', {'JUPITER_BUILD_RPS': '1'}):
                for i in range(int(60 / interval)):
                    clock[0] = self.now + i * interval
                    jarvis.step(conn, state, self.market, quote, clock[0])
            self.assertTrue(all(b - a >= 1.049 for a, b in zip(starts, starts[1:])))
            self.assertEqual(state['quote_activity']['deferrals'], 0)
            # One exact-size quote funds all eight eligible legacy/lab variants.
            self.assertEqual(state['quote_activity']['paper_entries'], 8 * state['quote_activity']['entry_quotes'])
            self.assertGreater(state['quote_activity']['exit_quotes'], 0)
            return state['quote_activity']['entry_quotes']

        self.assertGreaterEqual(run(jarvis.WORKER_INTERVAL), 1.5 * run(2))

    def test_instant_entry_survives_quote_backlog_but_requires_fresh_observation(self):
        state = jarvis.initial()
        jarvis.observe(state, self.market, self.now)
        with jarvis.connection() as conn:
            jobs = jarvis.entry_jobs(conn, state, self.now + 80)
            self.assertTrue(jobs)
            for row in state['watch'].values():
                row['last'] = self.now + 120
            self.assertTrue(jarvis.entry_jobs(conn, state, self.now + 120))
            self.assertFalse(jarvis.entry_jobs(conn, state, self.now + 220))
            for row in state['watch'].values():
                row['last'] = self.now + 901
            self.assertFalse(jarvis.entry_jobs(conn, state, self.now + 901))

    def test_pullback_reuses_the_same_discovery_and_waits_for_rebound(self):
        state = jarvis.initial()
        jarvis.observe(state, self.market, self.now)
        self.market.price = .93
        jarvis.observe(state, self.market, self.now + 20)
        self.assertIsNone(state['watch'][MINT]['pullback'])
        self.market.price = .96
        jarvis.observe(state, self.market, self.now + 40)
        self.assertEqual(state['watch'][MINT]['pullback'], self.now + 40)

    def test_trim_requires_size_specific_quote_and_banks_partial_proceeds(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 120_000, 'age_minutes': 300}
        bot = 'instant-trim'
        jarvis.open_trade(state, bot, row, 100, self.now)
        portfolio = state['bots'][bot]
        position = portfolio['positions'][MINT]
        completed = []
        jarvis.apply_exit(completed, portfolio, bot, position, 100, 65_000_000, self.now)
        self.assertEqual(position['pending'], 'trim')
        self.assertEqual(position['quantity'], 100)
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 32_000_000, self.now)
        self.assertEqual(position['quantity'], 50)
        self.assertEqual(position['target'], 40)
        self.assertEqual(position['banked'], 31_900_000)
        self.assertFalse(completed)

    def test_fifth_profit_take_requires_quote_for_full_remainder(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 120_000, 'age_minutes': 300}
        bot = 'instant-trim'
        jarvis.open_trade(state, bot, row, 160, self.now)
        portfolio = state['bots'][bot]
        position = portfolio['positions'][MINT]
        completed = []
        now = self.now + jarvis.POLL

        for target in (20, 40, 80, 160):
            quantity, cost = position['quantity'], position['cost']
            amount = math.ceil(cost * (1 + target / 100)) + jarvis.SELL_FEE + 1
            jarvis.apply_exit(completed, portfolio, bot, position, quantity, amount, now)
            self.assertEqual(position['pending'], 'trim')
            jarvis.apply_exit(completed, portfolio, bot, position, quantity // 2, amount // 2 + jarvis.SELL_FEE, now)
            self.assertFalse(completed)

        self.assertEqual((position['quantity'], position['trims'], position['target']), (10, 4, 320))
        position['pending'] = 'trim'
        self.assertEqual(list(jarvis.quote_jobs(state, now)), [(MINT, 10)])
        amount = math.ceil(position['cost'] * 4.2) + jarvis.SELL_FEE + 1
        jarvis.apply_exit(completed, portfolio, bot, position, 5, amount, now)
        self.assertEqual(position['quantity'], 10)
        jarvis.apply_exit(completed, portfolio, bot, position, 10, amount, now)
        self.assertNotIn(MINT, portfolio['positions'])
        self.assertEqual(completed[0]['reason'], 'Took profit · fifth sell closed remainder')

    def test_trim_steps_up_to_ten_percent_and_closes_the_remainder(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 120_000, 'age_minutes': 300}
        bot = 'patient-trim'
        jarvis.open_trade(state, bot, row, 100, self.now)
        portfolio = state['bots'][bot]
        position = portfolio['positions'][MINT]
        completed = []

        # A net +20% full-size quote raises the floor to +10% and queues a trim.
        jarvis.apply_exit(completed, portfolio, bot, position, 100, 60_400_000, self.now)
        self.assertEqual(position['peak'], 20)
        self.assertEqual(position['pending'], 'trim')
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 30_250_000, self.now + 2)
        self.assertEqual(position['quantity'], 50)
        self.assertEqual(position['target'], 40)

        # A +12% quote stays above the ratcheted stop. A +9% quote closes it.
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 28_240_000, self.now + 4)
        self.assertFalse(completed)
        self.assertEqual(position['peak'], 20)
        jarvis.apply_exit(completed, portfolio, bot, position, 50, 27_486_250, self.now + 6)
        self.assertNotIn(MINT, portfolio['positions'])
        self.assertEqual(completed[0]['reason'], 'Step profit protection')
        self.assertEqual(completed[0]['pnl'], 7_286_250)

    def test_reviews_start_at_1000_then_use_only_new_batches_of_500(self):
        self.assertEqual(jarvis.status()['review_every'], 1000)
        with jarvis.connection() as conn, conn:
            state = jarvis.initial()

            def close_batch(start, end, winner):
                for index in range(start, end):
                    bot = jarvis.FIXED[index % 4]
                    mint = f'test-{index // 4}'
                    pnl = 1_000_000 if bot == winner else -1_000_000
                    row = {'bot': bot, 'mint': mint, 'liquidity': 150_000, 'age': 200, 'pnl': pnl}
                    conn.execute(
                        'INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                        (bot, mint, index, index, '2026-11-19', pnl, jarvis.BUY_COST, json.dumps(row)),
                    )
                jarvis.evaluate(conn, state)

            close_batch(0, 500, 'patient-step')
            self.assertIsNone(state['strategy'])
            close_batch(500, 999, 'patient-step')
            self.assertIsNone(state['strategy'])
            close_batch(999, 1000, 'patient-step')
            self.assertEqual(state['strategy']['version'], 1000)
            self.assertEqual(state['strategy']['entry'], 'pullback')
            self.assertEqual(state['strategy']['exit'], 'step')

            close_batch(1000, 1499, 'instant-trim')
            self.assertEqual(state['strategy']['version'], 1000)
            close_batch(1499, 1500, 'instant-trim')
            self.assertEqual(state['strategy']['version'], 1500)
            self.assertEqual(state['strategy']['entry'], 'instant')
            self.assertEqual(state['strategy']['exit'], 'trim')

            close_batch(1500, 2000, None)
            self.assertEqual(state['reviewed'], 2000)
            self.assertEqual(state['strategy']['version'], 1500)
            self.assertIn('kept its current strategy', state['review']['result'])
            self.assertEqual(state['bots']['jarvis']['cash'], jarvis.JARVIS_BUDGET)
            self.assertEqual([jarvis.BOTS[bot][1] for bot in jarvis.FIXED[:4]],
                             ['instant', 'instant', 'pullback', 'pullback'])

            conn.execute('UPDATE run SET state=? WHERE id=1', (json.dumps(state),))
        self.assertEqual(jarvis.status()['review_every'], 500)

    def test_jarvis_cannot_auto_fund(self):
        state = jarvis.initial()
        state['bots']['jarvis']['cash'] = jarvis.BUY_COST - 1
        row = {'mint': MINT, 'symbol': 'Example', 'liquidity_usd': 150_000, 'age_minutes': 300}
        jarvis.open_trade(state, 'jarvis', row, 100, self.now)
        self.assertFalse(state['bots']['jarvis']['positions'])
        self.assertEqual(state['bots']['jarvis']['funding'], jarvis.JARVIS_BUDGET)


if __name__ == '__main__':
    unittest.main()
