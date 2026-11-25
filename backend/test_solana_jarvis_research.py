from contextlib import closing
from datetime import datetime, timezone
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
