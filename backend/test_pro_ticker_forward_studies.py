"""Future-window registration, no-look-ahead references and immutable replay."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import asyncio
import json
import tempfile
import unittest
from unittest.mock import Mock,patch
import uuid
import httpx
from fastapi import FastAPI,HTTPException
import database
from pro_ticker_broker_admission import PaperEvidenceContext
from pro_ticker_manual_signals import submit_manual_signal
from pro_ticker_research_evidence import capture_research
from pro_ticker_forward_studies import (register_study,evaluate_study,study_report,study_history,
    choose_session_window,StudyNotDue,StudyConflict,METHODS,VERSION)
from pro_ticker_execution_routes import build_paper_execution_router
from pro_ticker_paper_gateway import AlpacaPaperEvidenceGateway,PaperGatewayUnavailable
import test_pro_ticker_manual_signals as fixtures

NOW=datetime(2026,10,26,14,0,10,tzinfo=timezone.utc)
ACCOUNT='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def daily(symbol,**kw):
    days=[NOW-timedelta(days=i) for i in range(100,0,-1) if (NOW-timedelta(days=i)).weekday()<5][-60:]
    return {'symbol':symbol,'source':'yahoo_finance_daily','bars':[{'timestamp':d.isoformat(),'open':9,'high':10,'low':8,'close':9,'volume':100000} for d in days]}


def path_for(symbol,start,end):
    moment=datetime.fromisoformat(start)
    bars=[{'t':(moment+timedelta(minutes=i)).isoformat(),'o':'10.2','h':'10.6','l':'9.8','c':'10.4','v':1000} for i in range(60)]
    bars[0]['c']='10.1';bars[2]['o']='10.3';bars[30]['c']='9.9'
    return {'symbol':symbol,'bars':bars,'next_page_token':None}


class ForwardStudyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        patched=patch.object(database,'DATABASE_PATH',Path(temp.name)/'studies.db');patched.start();self.addCleanup(patched.stop)
        self.now=NOW.timestamp();self.signal_id=str(uuid.uuid4());self.gateway=Mock()
        self.gateway.paper=True;self.gateway.base_url='https://paper-api.alpaca.markets';self.gateway.account_id=ACCOUNT
        self.gateway.read_account.return_value={'id':ACCOUNT}
        self.gateway.read_research_calendar.return_value=[{'date':'2026-10-26','open':'09:30','close':'16:00'}]
        self.gateway.read_research_bars.side_effect=path_for
        self.gateway.read_research_quote.side_effect=lambda symbol,start,end:{'t':start,'bp':'10.1','ap':'10.2'}
        self.context=PaperEvidenceContext(ACCOUNT,lambda:self.gateway,True)
        self.review=submit_manual_signal({'symbol':'TEST','request_id':self.signal_id},
            quote_fetcher=lambda symbol:{'p':10.5,'t':NOW.isoformat()},analyzer=lambda symbol:fixtures.analysis(symbol),clock=lambda:self.now,
            research_collector=lambda symbol,reference:capture_research(symbol,reference,fetch_history=daily,fetch_news=lambda s:None,clock=lambda:self.now))

    def register(self):return register_study(self.signal_id,context=self.context,clock=lambda:self.now)
    def evaluate(self):return evaluate_study(self.signal_id,context=self.context,clock=lambda:self.now)
    def due(self,plan):self.now=datetime.fromisoformat(plan['due_at']).timestamp()+1

    def test_registration_freezes_future_rules_and_retries_do_not_replan(self):
        self.gateway.read_research_calendar.return_value[0]['untrusted']='private-value'
        first=self.register();plan=first['plan'];self.assertNotIn('private-value',json.dumps(plan));self.assertGreater(datetime.fromisoformat(plan['start']).timestamp(),self.now)
        self.assertEqual(plan['methods'],list(METHODS));self.assertFalse(plan['order_execution_authorized'])
        self.gateway.read_research_calendar.side_effect=AssertionError('must not replan')
        self.assertEqual(self.register()['plan'],plan)
        self.assertEqual(study_history()['total_count'],1)
        other=PaperEvidenceContext(str(uuid.uuid4()),lambda:self.gateway,True)
        with self.assertRaises(ValueError):register_study(self.signal_id,context=other,clock=lambda:self.now)
        self.gateway.submit_bracket.assert_not_called()

    def test_early_evaluation_never_fetches_future_prices(self):
        self.register()
        with self.assertRaises(StudyNotDue):self.evaluate()
        self.gateway.read_research_bars.assert_not_called()
        self.assertIsNone(study_history()['studies'][0]['report'])

    def test_confirmations_enter_at_next_open_and_report_spread_without_net_claim(self):
        plan=self.register()['plan'];self.due(plan);result=self.evaluate();report=result['report']
        one,two=report['outcomes']
        self.assertEqual(one['entry_at'],(datetime.fromisoformat(plan['start'])+timedelta(minutes=1)).isoformat())
        self.assertEqual(two['entry_at'],(datetime.fromisoformat(plan['start'])+timedelta(minutes=2)).isoformat())
        self.assertEqual(one['entry_reference'],'10.2');self.assertEqual(two['entry_reference'],'10.3')
        self.assertTrue(one['false_breakout']);self.assertIsNotNone(one['quoted_spread_percent'])
        self.assertIsNone(one['net_pnl']);self.assertIsNone(one['trade_pnl'])
        self.assertFalse(report['training_eligible']);self.assertFalse(report['model_updated'])
        history=study_history();self.assertEqual(history['groups'][0]['triggered'],1)
        self.assertEqual(history['studies'][0]['report'],report)
        self.gateway.read_research_bars.side_effect=AssertionError('immutable result')
        self.assertEqual(self.evaluate()['report'],report)

    def test_incomplete_wrong_symbol_future_or_adjusted_page_cannot_create_outcome(self):
        plan=self.register()['plan'];self.due(plan)
        original=path_for('TEST',plan['start'],plan['end_exclusive'])
        cases=[{**original,'symbol':'OTHER'},{**original,'next_page_token':'more'},
               {**original,'bars':original['bars'][:-1]}]
        duplicate=deepcopy(original);duplicate['bars'][1]['t']=duplicate['bars'][0]['t'];cases.append(duplicate)
        bad=deepcopy(original);bad['bars'][0]['h']='1';cases.append(bad)
        bad=deepcopy(original);bad['bars'][0]['v']=True;cases.append(bad)
        for raw in cases:
            self.gateway.read_research_bars.side_effect=None;self.gateway.read_research_bars.return_value=raw
            with self.assertRaises((ValueError,ArithmeticError)):self.evaluate()
            self.assertIsNone(study_history()['studies'][0]['report'])
            self.assertEqual(study_history()['studies'][0]['last_error'],'provider_or_evidence_unavailable')

    def test_no_signal_is_not_a_zero_return_or_a_breakeven(self):
        plan=self.register()['plan'];self.due(plan)
        raw=path_for('TEST',plan['start'],plan['end_exclusive'])
        for bar in raw['bars']:bar.update(o='9.9',h='10',l='9.8',c='9.9')
        self.gateway.read_research_bars.side_effect=None;self.gateway.read_research_bars.return_value=raw
        self.evaluate();group=study_history()['groups'][0]
        self.assertEqual(group['not_triggered'],1);self.assertEqual(group['triggered'],0)
        self.assertIsNone(group['mean_quote_return_percent']);self.assertEqual(group['flat'],0)

    def test_negative_quote_outcomes_remain_in_groups(self):
        plan=self.register()['plan'];self.due(plan)
        raw=path_for('TEST',plan['start'],plan['end_exclusive']);raw['bars'][-1].update(c='9.8',l='9.7')
        self.gateway.read_research_bars.side_effect=None;self.gateway.read_research_bars.return_value=raw
        self.evaluate();group=study_history()['groups'][0]
        self.assertEqual(group['negative'],1);self.assertEqual(group['positive'],0)
        self.assertLess(float(group['mean_quote_return_percent']),0)

    def test_paired_review_threshold_does_not_apply_a_strategy_change(self):
        plan=self.register()['plan'];self.due(plan);self.evaluate()
        with closing(database.get_connection()) as conn:
            evidence=json.loads(conn.execute('SELECT evidence_json FROM pro_ticker_forward_study_outcomes').fetchone()[0])
            for i in range(19):
                clone={**plan,'signal_id':str(uuid.uuid4()),'symbol':'TEST'+str(i)}
                varied=deepcopy(evidence);varied['path']['symbol']=clone['symbol'];report=study_report(clone,varied)
                if i==18:self.assertIsNone(study_history()['paired_comparison']['review_candidate_method'])
                conn.execute('INSERT INTO pro_ticker_forward_studies(signal_id,plan_json) VALUES (?,?)',(clone['signal_id'],json.dumps(clone)))
                conn.execute('INSERT INTO pro_ticker_forward_study_outcomes VALUES (?,?,?,?)',(clone['signal_id'],evidence['observed_at'],json.dumps(varied),json.dumps(report)))
                conn.commit()
        result=study_history()['paired_comparison']
        self.assertEqual(result['paired_triggered_count'],20);self.assertEqual(result['review_candidate_method'],METHODS[0])
        self.assertTrue(result['requires_explicit_approval']);self.assertFalse(result['applied'])

    def test_duplicate_symbol_windows_do_not_inflate_comparison_samples(self):
        plan=self.register()['plan'];self.due(plan);self.evaluate()
        with closing(database.get_connection()) as conn:
            evidence=json.loads(conn.execute('SELECT evidence_json FROM pro_ticker_forward_study_outcomes').fetchone()[0])
            clone={**plan,'signal_id':str(uuid.uuid4())};report=study_report(clone,evidence)
            conn.execute('INSERT INTO pro_ticker_forward_studies(signal_id,plan_json) VALUES (?,?)',(clone['signal_id'],json.dumps(clone)))
            conn.execute('INSERT INTO pro_ticker_forward_study_outcomes VALUES (?,?,?,?)',(clone['signal_id'],evidence['observed_at'],json.dumps(evidence),json.dumps(report)));conn.commit()
        history=study_history();self.assertEqual(history['window_count'],2)
        self.assertEqual(history['duplicate_window_count'],1);self.assertEqual(history['paired_comparison']['paired_triggered_count'],1)
        self.assertEqual(history['groups'][0]['evaluated_windows'],1)

    def test_quote_failure_does_not_invent_a_spread_or_discard_valid_bars(self):
        plan=self.register()['plan'];self.due(plan);self.gateway.read_research_quote.side_effect=RuntimeError('private-secret')
        report=self.evaluate()['report'];self.assertIsNone(report['outcomes'][0]['quoted_spread_percent'])
        self.assertNotIn('private-secret',json.dumps(report))

    def test_stale_review_or_wrong_account_blocks_registration(self):
        self.now+=301
        with self.assertRaises(ValueError):self.register()
        self.gateway.read_research_calendar.assert_not_called()
        self.now=NOW.timestamp();self.gateway.read_account.return_value={'id':str(uuid.uuid4())}
        with self.assertRaises(ValueError):self.register()

    def test_active_claim_and_expired_worker_cannot_duplicate_or_publish(self):
        plan=self.register()['plan'];self.due(plan)
        with closing(database.get_connection()) as conn:
            conn.execute('UPDATE pro_ticker_forward_studies SET lease_until=?,lease_owner=?',(self.now+90,'other'));conn.commit()
        with self.assertRaises(StudyConflict):self.evaluate()
        self.gateway.read_research_bars.assert_not_called()
        self.now+=91;started=self.now
        def late(symbol,start,end):self.now=started+91;return path_for(symbol,start,end)
        self.gateway.read_research_bars.side_effect=late
        with self.assertRaises(StudyConflict):self.evaluate()
        self.assertIsNone(study_history()['studies'][0]['report'])

    def test_replay_detects_report_tampering_and_methods_cannot_change(self):
        plan=self.register()['plan'];self.due(plan);self.evaluate()
        with closing(database.get_connection()) as conn:
            row=conn.execute('SELECT report_json FROM pro_ticker_forward_study_outcomes').fetchone()
            value=json.loads(row[0]);value['outcomes'][0]['quote_return_percent']='999'
            conn.execute('UPDATE pro_ticker_forward_study_outcomes SET report_json=?',(json.dumps(value),));conn.commit()
        with self.assertRaises(ValueError):study_history()
        with self.assertRaises(ValueError):self.evaluate()
        bad={**plan,'methods':['future_best_method']}
        with self.assertRaises(ValueError):study_report(bad,{'observed_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat(),'path':path_for('TEST',plan['start'],plan['end_exclusive'])})

    def test_saved_study_and_outcome_survive_disposable_copy_validation(self):
        from validate_paper_database_copy import validate_database_backup
        plan=self.register()['plan'];self.due(plan);self.evaluate();before=study_history()
        result=validate_database_backup(database.DATABASE_PATH)
        self.assertTrue(result['valid']);self.assertTrue(result['existing_values_preserved'])
        self.assertEqual(study_history(),before)

    def test_future_quote_research_can_use_fresh_daily_history_without_a_live_trade_reference(self):
        self.signal_id=str(uuid.uuid4())
        submit_manual_signal({'symbol':'TEST','request_id':self.signal_id},quote_fetcher=lambda symbol:None,
            analyzer=lambda symbol:fixtures.analysis(symbol),clock=lambda:self.now,
            research_collector=lambda symbol,reference:capture_research(symbol,reference,fetch_history=daily,fetch_news=lambda s:None,clock=lambda:self.now))
        plan=self.register()['plan'];self.assertTrue(plan['research_only']);self.assertFalse(plan['order_execution_authorized'])
        self.gateway.submit_bracket.assert_not_called()

    def test_early_close_rolls_to_the_next_known_full_session(self):
        registered='2026-11-27T17:30:00+00:00'
        calendar=[{'date':'2026-11-27','open':'09:30','close':'13:00'},{'date':'2026-11-30','open':'09:30','close':'16:00'}]
        window=choose_session_window(calendar,registered_at=registered)
        self.assertEqual(window['start'],'2026-11-30T14:30:00+00:00')
        with self.assertRaises(ValueError):choose_session_window([calendar[0],calendar[0]],registered_at=registered)

    def test_http_security_and_browser_outcomes_are_not_accepted(self):
        def auth(request):
            if request.headers.get('x-owner')!='yes':raise HTTPException(401)
        app=FastAPI();app.include_router(build_paper_execution_router(require_session=auth,context=self.context))
        async def run():
            base='/auto-trader/pro-ticker/manual-signals/'+self.signal_id+'/forward-study';headers={'x-owner':'yes','Origin':'https://test'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://test') as c:
                self.assertEqual((await c.get('/auto-trader/pro-ticker/forward-studies')).status_code,401)
                self.assertEqual((await c.post(base,json={},headers={**headers,'Origin':'https://evil'})).status_code,403)
                self.assertEqual((await c.post(base,json={'future_return':100},headers=headers)).status_code,422)
                self.assertEqual((await c.post(base+'/evaluate',json={'bars':[]},headers=headers)).status_code,422)
        asyncio.run(run());self.gateway.submit_bracket.assert_not_called()


class ForwardGatewayTests(unittest.TestCase):
    def gateway(self,get):
        return AlpacaPaperEvidenceGateway(account_id=ACCOUNT,key_id='synthetic',secret_key='synthetic',http_get=get)

    def test_raw_bars_use_exact_symbol_and_exclude_the_end_minute(self):
        from urllib.parse import urlsplit,parse_qs
        get=Mock(return_value=Mock(status_code=200,json=lambda:{'symbol':'TEST','bars':[],'next_page_token':None}))
        self.gateway(get).read_research_bars('TEST','2026-10-26T14:00:00Z','2026-10-26T15:00:00Z')
        query=parse_qs(urlsplit(get.call_args.args[0]).query)
        self.assertEqual(query['adjustment'],['raw']);self.assertEqual(query['asof'],['-'])
        self.assertEqual(query['end'],['2026-10-26T14:59:59.999999+00:00'])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        for value in [{'symbol':'OTHER','bars':[]},{'symbol':'TEST','bars':[],'next_page_token':'more'}]:
            get.return_value=Mock(status_code=200,json=lambda value=value:value)
            with self.assertRaises(PaperGatewayUnavailable):self.gateway(get).read_research_bars('TEST','2026-10-26T14:00:00Z','2026-10-26T15:00:00Z')

    def test_calendar_and_first_quote_requests_are_bounded(self):
        get=Mock(return_value=Mock(status_code=200,json=lambda:[]));gateway=self.gateway(get)
        self.assertEqual(gateway.read_research_calendar('2026-10-26','2026-11-02'),[])
        with self.assertRaises(ValueError):gateway.read_research_calendar('2026-10-26','2026-12-01')
        get.return_value=Mock(status_code=200,json=lambda:{'symbol':'TEST','quotes':[{'t':'2026-10-26T14:00:00Z','bp':10,'ap':10.1}],'next_page_token':'other_quotes'})
        self.assertEqual(gateway.read_research_quote('TEST','2026-10-26T14:00:00Z','2026-10-26T14:00:02Z')['bp'],10)
        self.assertIn('limit=1',get.call_args.args[0])
