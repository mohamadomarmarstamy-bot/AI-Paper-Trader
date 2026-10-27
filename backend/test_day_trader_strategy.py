from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import database
from day_trader_strategy import bars, evaluate, simulate
from day_trader_lab import observe, evaluate_saved, status, build_router, history

OPEN = datetime(2026,10,27,13,30,tzinfo=timezone.utc).timestamp()
CLOSE = OPEN+390*60
NOW = OPEN+365


def iso(t):
    return datetime.fromtimestamp(t,timezone.utc).isoformat()


def bar(t, **changes):
    return {'t':iso(t), 'o':10, 'h':10.1, 'l':9.8, 'c':10.05, 'v':200, **changes}


def fixture():
    raw = [bar(OPEN+i*60) for i in range(6)]
    raw[-1].update(o=10.05,h=10.25,c=10.2,l=10)
    quote = {'feed':'iex','quote_fresh':True,'quote_timestamp':iso(NOW),'ask':10.21,'bid':10.2}
    return raw, quote


class IntradayTests(unittest.TestCase):
    def decision(self, raw=None, quote=None, **kw):
        default_raw, default_quote = fixture()
        return evaluate(raw if raw is not None else default_raw,quote or default_quote,
                        session_open=OPEN,session_close=CLOSE,observed_at=NOW,
                        opening_volumes=kw.pop('opening_volumes',[500]*14),**kw)

    def test_breakout_and_risk_budget(self):
        result = self.decision()
        plan = result['setups'][0]
        self.assertEqual(plan['status'],'candidate')
        self.assertLessEqual(plan['shares']*10.21*1.001,2000)
        self.assertLessEqual(plan['planned_risk_usd'],20)
        self.assertIsInstance(plan['shares'],int)
        self.assertFalse(result['order_execution_authorized'])

    def test_vwap_pullback_is_independent_completed_bar_hypothesis(self):
        raw = [bar(OPEN+i*60,o=10.1,h=10.21,l=10.05,c=10.2) for i in range(21)]
        raw[-1].update(h=10.35,c=10.3,v=400)
        q = fixture()[1];q.update(ask=10.31,bid=10.30,quote_timestamp=iso(OPEN+1265))
        result = evaluate(raw,q,session_open=OPEN,session_close=CLOSE,observed_at=OPEN+1265,opening_volumes=[500]*14)
        self.assertEqual(result['setups'][1]['status'],'candidate')

    def test_no_chase_no_volume_no_fabricated_history(self):
        q = fixture()[1];q['ask']=11
        self.assertEqual(self.decision(quote=q)['setups'][0]['status'],'no_setup')
        for volumes in ([],[500]*13,[5000]*14):
            self.assertEqual(self.decision(opening_volumes=volumes)['setups'][0]['status'],'no_setup')

    def test_partial_future_bar_cannot_change_decision(self):
        raw,q = fixture()
        partial = bar(OPEN+360,o=100,h=110,l=90,c=105,v=99999)
        self.assertEqual(self.decision(raw=raw+[partial]),self.decision(raw=raw))

    def test_gaps_stale_quotes_and_crossed_quotes_are_unavailable(self):
        raw,q = fixture()
        self.assertEqual(self.decision(raw=raw[:2]+raw[3:])['status'],'unavailable')
        q['quote_timestamp']=iso(NOW-31)
        self.assertEqual(self.decision(quote=q)['status'],'unavailable')
        q['quote_timestamp']=iso(NOW);q['bid']=11
        self.assertEqual(self.decision(quote=q)['status'],'unavailable')

    def test_duplicate_and_unsorted_bars_rejected(self):
        raw,_ = fixture()
        for changed in (raw+raw[-1:],raw[::-1]):
            with self.assertRaises(ValueError):
                bars(changed,observed_at=NOW)

    def path(self, plan):
        start = (int(NOW//60)+1)*60
        return [bar(t,o=10.21,h=10.3,l=10.1,c=10.22) for t in range(start,int(plan['expires_at']),60)]

    def test_pending_missing_path_and_no_same_bar_lookahead(self):
        p = self.decision()['setups'][0]
        self.assertEqual(simulate(p,[],frozen_at=NOW,observed_at=NOW)['status'],'pending')
        path = self.path(p)
        self.assertEqual(simulate(p,path[1:],frozen_at=NOW,observed_at=p['expires_at']+61)['status'],'inconclusive')
        path.insert(0,bar(OPEN+360,o=10,h=100,l=1,c=10))
        result = simulate(p,path,frozen_at=NOW,observed_at=p['expires_at']+61)
        self.assertEqual(result['exit_reason'],'time_exit')
        self.assertGreater(result['scenarios'][0]['modeled_pnl_usd'],result['scenarios'][2]['modeled_pnl_usd'])

    def test_stop_first_and_gap_price_are_conservative(self):
        p = self.decision()['setups'][0];path = self.path(p)
        path[0].update(h=p['target']+1,l=p['stop']-1)
        result = simulate(p,path,frozen_at=NOW,observed_at=p['expires_at']+61)
        self.assertEqual(result['exit_reason'],'stop_first_if_ambiguous')
        path[0].update(o=9,h=11,l=8,c=10)
        result = simulate(p,path,frozen_at=NOW,observed_at=p['expires_at']+61)
        self.assertEqual(result['exit_reason'],'gap_below_stop')
        self.assertLess(result['scenarios'][0]['modeled_pnl_usd'],-20)

    def test_early_close_cuts_entry_window(self):
        raw,q = fixture()
        result = evaluate(raw,q,session_open=OPEN,session_close=NOW+1799,observed_at=NOW,opening_volumes=[500]*14)
        self.assertEqual(result['status'],'unavailable')


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p = patch.object(database,'DATABASE_PATH',Path(temp.name)/'test.sqlite3');p.start();self.addCleanup(p.stop)
        self.now=NOW;self.calls=[];self.raw,self.quote=fixture();self.request_id=str(uuid.uuid4())
        q = patch('day_trader_lab.read_quote', side_effect=lambda *a,**kw: {**self.quote,'quote_timestamp':iso(self.now)})
        q.start();self.addCleanup(q.stop)
        for day in range(1,15):
            self.raw[:0]=[bar(OPEN-day*86400+i*60,v=100) for i in range(5)]
    def data(self, method,path,**kwargs):
        self.calls.append((method,path,kwargs));self.assertEqual(method,'GET')
        self.assertEqual(kwargs['params'].get('feed'),'iex')
        if path.endswith('/bars'):return {'bars':self.raw,'next_page_token':None}
        return {'TEST':{'latestQuote':{'t':iso(self.now),'ap':10.21,'bp':10.20}}}
    def broker(self, method,path,**kwargs):
        self.assertEqual((method,path),('GET','/v2/calendar'))
        return [{'date':'2026-10-27','open':'09:30','close':'16:00'}]
    def observe(self):
        return observe({'symbol':'TEST','request_id':self.request_id},data_request=self.data,
                       broker_read=self.broker,feed='iex',clock=lambda:self.now)
    def test_freeze_retries_do_not_refetch_or_rewrite(self):
        first=self.observe();count=len(self.calls)
        self.now+=10
        self.assertEqual(self.observe()['results'],first['results'])
        self.assertEqual(len(self.calls),count)
        self.assertFalse(first['orders_enabled'])
    def test_outcome_remains_pending_without_future_history(self):
        self.observe();count=len(self.calls)
        result=evaluate_saved(self.request_id,data_request=self.data,clock=lambda:self.now)
        self.assertIsNone(result['results'][0]['outcomes'])
        self.assertEqual(len(self.calls),count)
    def test_complete_forward_path_is_saved_once(self):
        self.observe();p=status()['results'][0]['decision']['setups'][0]
        self.assertEqual(p['status'],'candidate')
        self.raw=IntradayTests().path(p);self.now=p['expires_at']+61
        result=evaluate_saved(self.request_id,data_request=self.data,clock=lambda:self.now)
        self.assertEqual(result['results'][0]['outcomes'][0]['status'],'modeled')
        count=len(self.calls)
        evaluate_saved(self.request_id,data_request=self.data,clock=lambda:self.now)
        self.assertEqual(count,len(self.calls))
    def test_pagination_cannot_silently_drop_later_symbols_or_bars(self):
        def pages(*a,**kw):return {'bars':[],'next_page_token':'more'}
        with self.assertRaises(ValueError):history('TEST',start=iso(OPEN),end=iso(NOW),feed='iex',data_request=pages)
    def test_http_auth_origin_and_body_boundaries(self):
        allowed=False
        def auth(request):
            if not allowed:raise HTTPException(401,'Owner session required.')
        app=FastAPI();app.include_router(build_router(require_session=auth,data_request=self.data,broker_read=self.broker,feed=lambda:'iex'))
        with TestClient(app) as client:
            self.assertEqual(client.get('/auto-trader/day-trader').status_code,401)
            allowed=True
            self.assertEqual(client.post('/auto-trader/day-trader/observe',json={}).status_code,403)
            self.assertEqual(client.post('/auto-trader/day-trader/observe',headers={'Origin':'http://testserver'},json={'symbol':'TEST','request_id':self.request_id,'ask':1}).status_code,422)
            self.assertEqual(client.get('/auto-trader/day-trader').json()['summary'][0]['modeled_samples'],0)
