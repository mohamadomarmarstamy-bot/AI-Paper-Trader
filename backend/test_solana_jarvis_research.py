from contextlib import closing
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import database
import jarvis_outcomes as outcomes
import jarvis_research as research
import solana_meme_learning as legacy
import jarvis_test_bot as jarvis

NOW = 1_800_000_000
MINT = '11111111111111111111111111111112'
PAIR = '11111111111111111111111111111113'


class JarvisResearchTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(database, 'DATABASE_PATH', Path(temp) / 'paper.db'))

    def test_only_closed_candles_count_for_breakout_and_retest(self):
        bars = [[NOW - (20 - i) * 60, 100, 101, 99, 100, 100] for i in range(18)]
        bars += [[NOW - 120, 100, 110, 100, 108, 300],
                 [NOW - 60, 104, 108, 101, 107, 110],
                 [NOW, 108, 500, 100, 499, 9000]]
        evidence = research.chart_signal(bars, NOW)
        self.assertEqual(evidence['pattern'], 'breakout_retest')
        self.assertEqual(evidence['closed_at'], NOW)
        self.assertIsNone(research.chart_signal(bars[:-1], NOW + 181))

    def test_chart_requests_provider_empty_intervals_without_relaxing_validation(self):
        bars=[[NOW-(20-i)*60,100,101,99,100+i*.01,100] for i in range(20)]
        def fetch(url,*,params):
            self.assertEqual(params['include_empty_intervals'],'true')
            self.assertEqual(params['token'],MINT)
            return {'data':{'attributes':{'ohlcv_list':bars}}}
        value=research.chart({'mint':MINT,'pair':PAIR},NOW,fetch)
        self.assertIsNotNone(value['rsi'])
        self.assertIsNone(research.chart_signal(bars[:5]+bars[6:],NOW))

    def prepare_chart_watch(self,count=3):
        rows={}
        for index in range(count):
            mint=MINT+str(index+1)
            rows[mint]={'mint':mint,'pair':PAIR,'symbol':'Coin','first':NOW-20+index,'last':NOW}
        with jarvis.connection() as conn,conn:
            state,_=jarvis.read(conn);state.update(enabled=True,watch=rows,local_rules={'research-01':{'created':NOW-30}})
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        return rows

    def test_chart_scheduler_prioritizes_research_and_rotates_within_budget(self):
        rows=self.prepare_chart_watch(3)
        with jarvis.connection() as conn,conn:
            state,_=jarvis.read(conn)
            state['watch'][MINT]={'mint':MINT,'pair':PAIR,'symbol':'Old','first':NOW-1000,'last':NOW,'monitor_only':True}
            conn.execute('UPDATE run SET state=?',(json.dumps(state),))
        called=[]
        for offset in (0,6,12,18):
            def chart(row,now):
                called.append(row['mint'])
                return {'closed_at':NOW,'rsi':50,'volume_ratio':1}
            jarvis.research_tick(clock=lambda:NOW+offset,chart=chart,social=lambda *_:None)
        self.assertEqual(set(called[:3]),set(rows))
        self.assertEqual(len(called[:3]),len(set(called[:3])))
        self.assertEqual(called[3],MINT)  # Old holdings only after fresh research coverage.
        with jarvis.connection() as conn,conn:
            tasks=jarvis.claim_research(conn,NOW+19)
            self.assertFalse(any(source=='chart' for source,_ in tasks))

    def test_chart_refresh_prefers_recoverable_coverage_but_reserves_exploration(self):
        rows = self.prepare_chart_watch(7)
        warm = list(rows)[:5]
        called = []
        with jarvis.connection() as conn, conn:
            for i, mint in enumerate(warm):
                stamp = NOW - 190 + i
                conn.execute('INSERT INTO research VALUES (?,?,?,?)', (mint, PAIR, stamp, json.dumps({
                    'chart': {'at': stamp, 'attempt_at': stamp,
                              'value': {'closed_at': stamp, 'rsi': 50, 'volume_ratio': 2}}})))
        def chart(row, now):
            called.append(row['mint'])
            return {'closed_at': now, 'rsi': 50, 'volume_ratio': 2}
        for offset in (0, 5, 6, 12, 18, 24, 30, 36):
            jarvis.research_tick(clock=lambda: NOW+offset, chart=chart, social=lambda *_: None)
        self.assertEqual(called[:3], warm[:3])
        self.assertNotIn(called[3], warm)  # A cold pool gets its turn despite remaining warm demand.
        self.assertEqual(called[4:6], warm[3:])
        self.assertEqual(set(called), set(rows))
        self.assertEqual(len(called), 7)  # The early call did not increase request rate.

    def test_healthy_holding_does_not_starve_uncovered_entry(self):
        rows = self.prepare_chart_watch(2)
        held, uncovered = rows
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['bots']['research-01']['positions'][held] = {}
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            def set_chart(closed):
                conn.execute('INSERT OR REPLACE INTO research VALUES (?,?,?,?)', (held, PAIR, NOW-70,
                    json.dumps({'chart': {'at': NOW-70, 'value': {'closed_at': closed}}})))
            set_chart(NOW-70)
            jobs = jarvis.claim_research(conn, NOW)
            self.assertEqual(next(row['mint'] for source, row in jobs if source == 'chart'), uncovered)
            set_chart(NOW-165)
            jobs = jarvis.claim_research(conn, NOW+6)
            self.assertEqual(next(row['mint'] for source, row in jobs if source == 'chart'), held)

    def test_empty_chart_backoff_does_not_starve_other_coins(self):
        self.prepare_chart_watch(2);called=[]
        def chart(row,now):called.append(row['mint']);return None
        for offset in (0,6,12):
            jarvis.research_tick(clock=lambda:NOW+offset,chart=chart,social=lambda *_:None)
        self.assertEqual(len(called),2)
        self.assertEqual(len(set(called)),2)
        with jarvis.connection() as conn:
            for row in conn.execute('SELECT evidence FROM research'):
                chart=json.loads(row['evidence'])['chart']
                self.assertEqual(chart['status'],'insufficient_history')
                self.assertIsNone(chart['value'])

    def test_failed_refresh_keeps_original_age_and_rate_limit_backs_off(self):
        rows=self.prepare_chart_watch(1);mint=next(iter(rows));row=rows[mint]
        old={'at':NOW-60,'value':{'closed_at':NOW-60,'rsi':25,'volume_ratio':2}}
        with jarvis.connection() as conn,conn:
            conn.execute('INSERT INTO research VALUES (?,?,?,?)',(mint,PAIR,NOW-60,json.dumps({'chart':old})))
        import requests
        response=requests.Response();response.status_code=429
        def chart(*_):raise requests.HTTPError(response=response)
        jarvis.research_tick(clock=lambda:NOW,chart=chart,social=lambda *_:None)
        with jarvis.connection() as conn,conn:
            stored=json.loads(conn.execute('SELECT evidence FROM research WHERE mint=?',(mint,)).fetchone()[0])['chart']
            self.assertEqual(stored['at'],NOW-60)
            self.assertEqual(stored['status'],'rate_limited')
            self.assertTrue(jarvis.entry_evidence(conn,row,NOW)['chart'])
            self.assertNotIn('chart',jarvis.entry_evidence(conn,row,NOW+121))
            self.assertFalse(any(source=='chart' for source,_ in jarvis.claim_research(conn,NOW+6)))

    def test_rate_limit_budget_persists_and_recovers_cautiously(self):
        with jarvis.connection() as conn,conn:
            self.assertEqual(research.chart_interval(conn),6)
            self.assertEqual(research.record_chart_result(conn,'rate_limited'),12)
            self.assertEqual(research.record_chart_result(conn,'rate_limited'),24)
        with jarvis.connection() as conn,conn:
            self.assertEqual(research.chart_interval(conn),24)
            for _ in range(4):research.record_chart_result(conn,'ready')
            self.assertEqual(research.chart_interval(conn),24)
            research.record_chart_result(conn,'ready')
            self.assertEqual(research.chart_interval(conn),23)
            for _ in range(10):research.record_chart_result(conn,'rate_limited')
            self.assertEqual(research.chart_interval(conn),60)

    def test_successful_responses_without_candles_recover_pace_not_trade_evidence(self):
        with jarvis.connection() as conn, conn:
            self.assertEqual(research.record_chart_result(conn, 'rate_limited'), 12)
            for status in ('ready', 'stale_candles', 'insufficient_history', 'ready'):
                self.assertEqual(research.record_chart_result(conn, status), 12)
            self.assertEqual(research.record_chart_result(conn, 'provider_error'), 12)
            for status in ('stale_candles', 'insufficient_history', 'ready', 'stale_candles'):
                self.assertEqual(research.record_chart_result(conn, status), 12)
            self.assertEqual(research.record_chart_result(conn, 'insufficient_history'), 11)
            self.assertEqual(research.record_chart_result(conn, 'rate_limited'), 22)

    def test_chart_refresh_cannot_replace_fresh_evidence_with_older_or_weaker_chart(self):
        rows = self.prepare_chart_watch(1)
        row = next(iter(rows.values()))
        good = {'closed_at': NOW-60, 'rsi': 55, 'volume_ratio': 2, 'provider': 'codex'}
        with jarvis.connection() as conn, conn:
            conn.execute('INSERT INTO research VALUES (?,?,?,?)', (row['mint'], PAIR, NOW-60,
                json.dumps({'chart': {'at': NOW-60, 'value': good}})))
        older = {'closed_at': NOW-120, 'rsi': 45, 'volume_ratio': 1}
        jarvis.research_tick(clock=lambda: NOW, chart=lambda *_: older, social=lambda *_: None)
        with jarvis.connection() as conn:
            self.assertEqual(jarvis.entry_evidence(conn, row, NOW)['chart'], good)
            record = json.loads(conn.execute('SELECT evidence FROM research').fetchone()[0])['chart']
            self.assertEqual(record['at'], NOW-60)
            self.assertEqual(record['attempt_at'], NOW)
            self.assertTrue(record['refresh_retained'])
            coverage = jarvis.chart_coverage(conn, jarvis.read(conn)[0], NOW)
            self.assertEqual(coverage['provider']['cached_pools_last_refresh_retained'], 1)
            self.assertNotIn('chart', jarvis.entry_evidence(conn, row, NOW+121))
        weaker = {**good, 'volume_ratio': None}
        prior = {'at': NOW-60, 'value': good}
        self.assertEqual(research.choose_chart_record(prior, weaker, NOW), prior)
        updated = {**good, 'closed_at': NOW}
        self.assertEqual(research.choose_chart_record(prior, updated, NOW)['value'], updated)

    def test_stale_response_lag_is_observed_without_admitting_chart(self):
        rows=self.prepare_chart_watch(1)
        bars=[[NOW-1200-(20-i)*60,100,101,99,100,100] for i in range(20)]
        def chart(row,now):return research._chart_signal(bars,now)
        jarvis.research_tick(clock=lambda:NOW,chart=chart,social=lambda *_:None)
        with jarvis.connection() as conn:
            record=json.loads(conn.execute('SELECT evidence FROM research').fetchone()[0])['chart']
            self.assertEqual(record['status'],'stale_candles')
            self.assertEqual(record['returned_lag_seconds'],1200)
            self.assertIsNone(record['value'])
            self.assertNotIn('chart',jarvis.entry_evidence(conn,next(iter(rows.values())),NOW))
            coverage=jarvis.chart_coverage(conn,jarvis.read(conn)[0],NOW)
            self.assertEqual(coverage['provider']['median_returned_lag_seconds'],1200)

    def test_chart_does_not_query_pool_that_cannot_have_enough_bars(self):
        rows = self.prepare_chart_watch(2)
        young, mature = rows
        rows[young]['age_minutes'] = 18.99
        rows[mature]['age_minutes'] = 19
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['watch'] = rows
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            coverage = jarvis.chart_coverage(conn, state, NOW)
        self.assertEqual(coverage['eligible'], 2)
        self.assertEqual(coverage['reasons']['pool_warming'], 1)
        self.assertEqual(coverage['reasons']['not_sampled'], 1)
        called = []
        def chart(row, now):
            called.append(row['mint'])
        for offset in (0, 6):
            jarvis.research_tick(clock=lambda: NOW + offset, chart=chart, social=lambda *_: None)
        self.assertEqual(called, [mature])
        rows[young]['age_minutes'] = 19
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['watch'] = rows
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
        jarvis.research_tick(clock=lambda: NOW + 12, chart=chart, social=lambda *_: None)
        self.assertEqual(called, [mature, young])

    def test_provider_unavailable_reasons_and_zero_baseline(self):
        def bars(close, volume):
            return [[close - (20-i)*60, 100, 101, 99, 100, volume] for i in range(20)]
        row = {'mint': MINT, 'pair': PAIR}
        for value, reason in (([], 'insufficient_history'), (bars(NOW, 1)[1:], 'insufficient_history'),
                              (bars(NOW-180, 1), 'stale_candles'), ({}, 'invalid_chart'), ([None], 'invalid_chart')):
            with self.subTest(reason=reason, value=type(value)), self.assertRaises(research.ChartUnavailable) as error:
                research.chart(row, NOW, lambda *_a, **_kw: {'data': {'attributes': {'ohlcv_list': value}}})
            self.assertEqual(error.exception.status, reason)
        zero = research.chart_signal(bars(NOW, 0), NOW)
        self.assertEqual(zero['rsi'], 50)
        self.assertIsNone(zero['volume_ratio'])
        result = research.chart_coverage([row], {MINT: (PAIR, {'chart': {'at': NOW, 'value': zero}})}, NOW)
        self.assertEqual(result['reasons']['zero_volume_baseline'], 1)
        self.assertEqual(result['reasons']['complete'], 0)

    def test_chart_coverage_checks_pool_and_both_freshness_clocks(self):
        rows = self.prepare_chart_watch(5)
        first, second, third, fourth, fifth = rows
        item = lambda at, closed, status='ready': {'chart': {'at': at, 'status': status,
                                                            'value': {'rsi': 50, 'volume_ratio': 1, 'closed_at': closed}}}
        records = {first: (PAIR, item(NOW, NOW-60)),
                   second: (PAIR, item(NOW, NOW-181)),
                   third: ('old-pool', item(NOW, NOW-60)),
                   fourth: (PAIR, item(NOW-181, NOW-60, 'provider_error')),
                   fifth: (PAIR, item(NOW, NOW-60, 'rate_limited'))}
        coverage = research.chart_coverage(list(rows.values()), records, NOW)
        self.assertEqual(coverage['eligible'], sum(coverage['reasons'].values()))
        self.assertEqual({reason: count for reason, count in coverage['reasons'].items() if count},
                         {'complete': 2, 'stale_evidence': 1, 'not_sampled': 1, 'provider_error': 1})

    def test_rate_limit_reserves_provider_cooldown_for_whole_chart_queue(self):
        rows = self.prepare_chart_watch(2)
        import requests
        response = requests.Response()
        response.status_code = 429
        response.headers['Retry-After'] = '150'
        def fail(*_):
            raise requests.HTTPError(response=response)
        jarvis.research_tick(clock=lambda: NOW, chart=fail, social=lambda *_: None)
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            for mint in rows:
                state['watch'][mint]['last'] = NOW + 145
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            self.assertFalse(any(source == 'chart' for source, _ in jarvis.claim_research(conn, NOW + 149)))
            self.assertTrue(any(source == 'chart' for source, _ in jarvis.claim_research(conn, NOW + 150)))
        future = format_datetime(datetime.fromtimestamp(NOW + 180, timezone.utc), usegmt=True)
        self.assertEqual(research.rate_limit_delay({'Retry-After': future}, NOW), 180)
        self.assertEqual(research.rate_limit_delay({'Retry-After': 'invalid'}, NOW), 60)
        self.assertEqual(research.rate_limit_delay({'Retry-After': '5'}, NOW), 60)

    def test_chart_errors_and_transient_failures_retry_on_different_delays(self):
        rows = self.prepare_chart_watch(2)
        attempted = []
        def chart(row, now):
            attempted.append(row['mint'])
            if row['mint'] == next(iter(rows)):
                raise research.ChartUnavailable('stale_candles')
            raise TimeoutError()
        for offset in (0, 6):
            jarvis.research_tick(clock=lambda: NOW + offset, chart=chart, social=lambda *_: None)
        with jarvis.connection() as conn:
            stored = {row['mint']: json.loads(row['evidence'])['chart'] for row in conn.execute('SELECT mint,evidence FROM research')}
        for status, delay in (('stale_candles', 120), ('provider_error', 30)):
            chart = next(record for record in stored.values() if record['status'] == status)
            self.assertEqual(chart['next_attempt'] - chart['attempt_at'], delay)
            self.assertIsNone(chart['value'])
        self.assertEqual(len(set(attempted)), 2)

    def test_status_reports_coverage_for_eligible_pool(self):
        rows = self.prepare_chart_watch(1)
        mint = next(iter(rows))
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['local_rules']['research-01'].update(entry='rsi > 50 and volume_ratio > 1')
            state['watch'][mint].update(pullback=None, age_minutes=30, liquidity_usd=100000,
                                        volume_m5_usd=1000, buys_m5=15, sells_m5=5, price_usd=1)
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            conn.execute('INSERT INTO research VALUES (?,?,?,?)', (mint, PAIR, NOW, json.dumps({
                'chart': {'at': NOW, 'value': {'closed_at': NOW, 'rsi': 55, 'volume_ratio': 1.5}}})))
        with patch.object(jarvis.time, 'time', return_value=NOW):
            report = jarvis.status()
        self.assertEqual(report['research']['chart_coverage']['eligible'], 1)
        self.assertEqual(report['research']['chart_coverage']['reasons']['complete'], 1)

    def test_x_deduplicates_and_does_not_treat_name_as_verified(self):
        ts = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()
        rows = [
            {'author_id': '1', 'text': MINT, 'created_at': ts},
            {'author_id': '1', 'text': f'solana {MINT}', 'created_at': ts},
            {'author_id': '2', 'text': 'Example on Solana', 'created_at': ts},
            {'author_id': '3', 'text': MINT + '11', 'created_at': ts},
        ]

        def fetch(url, *, params, headers):
            self.assertIn(MINT, params['query'])
            self.assertIn('"Example"', params['query'])
            self.assertTrue(url.startswith('https://api.x.com/'))
            return {'data': rows}

        with patch.dict('os.environ', {'JARVIS_X_BEARER_TOKEN': 'test'}):
            evidence = research.social_source('x', {'mint': MINT, 'symbol': 'Example'}, NOW, fetch)
        self.assertEqual(evidence, {'mint_authors': 1, 'name_only_authors': 1})
        self.assertFalse(research.allows('social_confirmed', {'x': evidence}))
        self.assertTrue(research.allows('social_confirmed', {'x': {'mint_authors': 2}}))

    def test_worker_updates_point_in_time_evidence_and_emails_once(self):
        with jarvis.connection() as conn, conn:
            state, _ = jarvis.read(conn)
            state['enabled'] = True
            state['watch'][MINT] = {'mint': MINT, 'pair': PAIR, 'symbol': 'Example', 'last': NOW, 'first': NOW}
            conn.execute('UPDATE run SET state=?', (json.dumps(state),))
            conn.execute('INSERT INTO research_mail(version,body,due) VALUES (1000,?,0)', ('proposal',))
        emailed = []

        def deliver(version, body):
            emailed.append((version, body))
            return True

        with patch.dict('os.environ', {'JARVIS_X_BEARER_TOKEN': ''}):
            for _ in range(2):
                jarvis.research_tick(clock=lambda: NOW,
                                     chart=lambda *_: {'pattern': 'breakout_retest', 'closed_at': NOW},
                                     social=lambda *_: {'mint_authors': 2}, deliver=deliver)
        with jarvis.connection() as conn:
            row = state['watch'][MINT]
            evidence = jarvis.entry_evidence(conn, row, NOW)
            self.assertTrue(research.allows('chart_and_social', evidence))
            self.assertEqual(jarvis.entry_evidence(conn, row, NOW - 1), {})
            self.assertEqual(jarvis.entry_evidence(conn, row, NOW + 181), {})
        self.assertEqual(emailed, [(1000, 'proposal')])

    def test_filter_must_beat_same_opportunities_and_ignore_largest_winner(self):
        tagged = [{'pnl': 100, 'entry_evidence': {'chart': {'pattern': 'breakout_retest'}}}] * 4
        trades = tagged + [{'pnl': -90}, {'pnl': -90}]
        self.assertEqual(jarvis.score_rule(trades, 'breakout_retest', 3), 60)
        self.assertIsNone(jarvis.score_rule(tagged, 'breakout_retest', 3))
        self.assertIsNone(jarvis.score_rule(trades, 'social_confirmed', 3))

    def test_review_promotes_chart_rule_with_held_out_coins(self):
        state = jarvis.initial()
        with jarvis.connection() as conn, conn:
            trades = []
            for index in range(jarvis.FIRST_REVIEW):
                good = index % 2 == 0
                evidence = {'chart': {'pattern': 'breakout_retest' if good else 'failed_breakout'}}
                mint = f'coin-{index}'
                pnl = 500_000 if good else -700_000
                data = {'mint': mint, 'bot': 'instant-step', 'pnl': pnl, 'liquidity': 10000,
                        'age': 1, 'entry_evidence': evidence}
                trades.append(('instant-step', mint, NOW, NOW + 60, 'test-day', pnl, jarvis.BUY_COST, json.dumps(data)))
            conn.executemany('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             trades)
            jarvis.evaluate(conn, state)

        self.assertEqual(state['strategy']['entry_filter'], 'breakout_retest')
        self.assertEqual(state['strategy']['version'], 1000)
        self.assertGreater(state['strategy']['validation_pnl'], 0)

    def test_early_review_uses_existing_closes_without_activating_jarvis(self):
        state = jarvis.initial()
        with jarvis.connection() as conn, conn:
            for index in range(595):
                mint = f'coin-{index}'
                pnl = -1_000_000
                data = {'mint': mint, 'opened': NOW + index, 'bot': 'instant-step', 'pnl': pnl,
                        'liquidity': 10000, 'age': 1}
                conn.execute('INSERT INTO trades(bot,mint,opened,closed,day,pnl,entry_cost,data) VALUES (?,?,?,?,?,?,?,?)',
                             ('instant-step', mint, NOW + index, NOW + 700, 'test-day', pnl, jarvis.BUY_COST,
                              json.dumps(data)))
            conn.execute('UPDATE run SET state=?', (json.dumps({**state, 'enabled': True, 'next_discovery': NOW + 70}),))

        jarvis.tick(clock=lambda: NOW)
        report = jarvis.status()
        preview = report['early_review']
        self.assertEqual(preview['closed'], 595)
        self.assertEqual(preview['chart'], 0)
        self.assertEqual(preview['social'], 0)
        self.assertIsNone(preview['candidate'])
        self.assertEqual(preview['bots']['instant-step']['net'], -595)
        self.assertIsNone(report['strategy'])
        self.assertEqual(report['reviewed'], 0)
        self.assertEqual(report['review_every'], 1000)

    def test_loss_rule_counts_winners_it_would_skip(self):
        retained = [{'mint': f'r{i}', 'pnl': 3_000_000, 'liquidity': 200000, 'age': 200}
                    for i in range(12)]
        losing_pattern = [{'mint': f'l{i}', 'pnl': -7_000_000, 'liquidity': 20000, 'age': 20}
                          for i in range(6)]
        self.assertGreater(jarvis.score_rule(retained + losing_pattern, 'baseline', 10,
                                             avoid='young_thin_pool'), 0)
        winning_conflict = {**losing_pattern[0], 'pnl': 50_000_000}
        self.assertIsNone(jarvis.score_rule(retained + losing_pattern[1:] + [winning_conflict],
                                            'baseline', 10, avoid='young_thin_pool'))

    def test_legacy_labeled_outcomes_stay_in_reference_sample(self):
        rows = [
            {'id': str(index), 'mint': f'old-{index}', 'closed_at': NOW + index,
             'pnl_usd': pnl, 'exit_source': source,
             'entry_features': {'liquidity_usd': 20000, 'age_minutes': 15}}
            for index, (pnl, source) in enumerate(((-5, 'automatic'), (9, 'automatic'), (6, 'manual')))
        ]
        with closing(database.get_connection()) as conn, conn:
            conn.executescript(legacy.SCHEMA)
            legacy.archive(conn, rows)
        report = outcomes.legacy_counts(database.DATABASE_PATH)
        self.assertEqual((report['wins'], report['losses']), (1, 1))
        jarvis.refresh_legacy_outcomes(NOW)
        status = jarvis.status()
        self.assertEqual(status['legacy_outcomes'], report)
        self.assertEqual(status['reviewed'], 0)
        self.assertIsNone(status['strategy'])

    def test_loss_rule_only_blocks_jarvis_when_strategy_uses_it(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'pair': PAIR, 'first': NOW, 'last': NOW, 'pullback': None,
               'liquidity_usd': 40000, 'age_minutes': 60}
        state['watch'][MINT] = row
        state['strategy'] = {'entry': 'instant', 'activated_at': NOW - 1,
                             'min_liquidity': 0, 'min_age': 0, 'avoid_pattern': 'young_thin_pool'}
        with jarvis.connection() as conn:
            self.assertEqual(jarvis.entry_jobs(conn, state, NOW)[MINT], ['instant-step', 'instant-trim', 'lab-five-minute', 'lab-breakeven', 'lab-tp5-sl10', 'lab-tp20-sl10'])
            row['age_minutes'] = 120
            self.assertEqual(jarvis.entry_jobs(conn, state, NOW)[MINT], ['instant-step', 'instant-trim', 'lab-five-minute', 'lab-breakeven', 'lab-tp5-sl10', 'lab-tp20-sl10', 'jarvis'])

    def test_validated_filter_controls_only_jarvis_entries(self):
        state = jarvis.initial()
        row = {'mint': MINT, 'pair': PAIR, 'first': NOW, 'last': NOW, 'pullback': None,
               'liquidity_usd': 100000, 'age_minutes': 60}
        state['watch'][MINT] = row
        state['strategy'] = {'entry': 'instant', 'activated_at': NOW - 1,
                             'min_liquidity': 0, 'min_age': 0, 'entry_filter': 'social_confirmed'}
        with jarvis.connection() as conn, conn:
            self.assertEqual(jarvis.entry_jobs(conn, state, NOW)[MINT], ['instant-step', 'instant-trim', 'lab-five-minute', 'lab-breakeven', 'lab-tp5-sl10', 'lab-tp20-sl10'])
            data = {'x': {'at': NOW, 'value': {'mint_authors': 2}}}
            conn.execute('INSERT INTO research(mint,pair,checked,evidence) VALUES (?,?,?,?)',
                         (MINT, PAIR, NOW, json.dumps(data)))
            self.assertEqual(jarvis.entry_jobs(conn, state, NOW)[MINT], ['instant-step', 'instant-trim', 'lab-five-minute', 'lab-breakeven', 'lab-tp5-sl10', 'lab-tp20-sl10', 'jarvis'])
