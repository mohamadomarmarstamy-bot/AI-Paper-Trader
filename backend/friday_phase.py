"""Forward, one-share extended-session probes. No network or broker transport."""
from contextlib import closing
from datetime import datetime, time as day_time
import json
import sqlite3
import time

import friday_replay
from day_trader_strategy import bars
from execution_quotes import parse_snapshot
from fast_mover_discovery import EASTERN
from friday_research import COHORT, ledger_path

_calendar = None
_latest_batch = None


def remember_calendar(entry):
    global _calendar
    _calendar = dict(entry) if isinstance(entry,dict) else None


def publish(batch, finished):
    global _latest_batch
    _latest_batch = (finished, batch)


def window(now, calendar):
    local = datetime.fromtimestamp(now, EASTERN)
    if not isinstance(calendar,dict) or calendar.get('date') != local.date().isoformat() or local.weekday() >= 5:
        return 'calendar_unverified', None, None
    try:
        opened = day_time.fromisoformat(calendar['open'])
        closed = day_time.fromisoformat(calendar['close'])
    except (ValueError, TypeError, KeyError):
        return 'calendar_unverified', None, None
    if opened != day_time(9,30) or closed not in (day_time(13),day_time(16)):
        return 'calendar_unverified', None, None
    def epoch(t):
        return datetime.combine(local.date(),t,EASTERN).timestamp()
    if epoch(day_time(8)) <= now < epoch(opened):
        return 'pre_market', epoch(day_time(8)), epoch(opened)
    if epoch(opened) <= now < epoch(closed):
        return 'regular_session', epoch(opened), epoch(closed)
    if closed == day_time(13) and now >= epoch(closed):
        return 'short_day_post_market_unverified', None, None
    if epoch(closed) <= now < epoch(day_time(17)):
        return 'after_hours', epoch(closed), epoch(day_time(17))
    return 'outside_verified_iex_window', None, None


SCHEMA = """
CREATE TABLE IF NOT EXISTS phase_bars (
    symbol TEXT, stamp REAL, candle TEXT NOT NULL, PRIMARY KEY(symbol,stamp)
);
CREATE TABLE IF NOT EXISTS phase_probes (
    intent TEXT PRIMARY KEY, symbol TEXT NOT NULL, phase TEXT NOT NULL,
    opened REAL NOT NULL, ask REAL NOT NULL, due REAL NOT NULL, state TEXT NOT NULL,
    exit_at REAL, bid REAL, net REAL, evidence TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase_health (
    id INTEGER PRIMARY KEY CHECK(id=1), phase TEXT NOT NULL, reason TEXT NOT NULL, checked REAL NOT NULL
);
"""


def tick(batch, calendar, *, now, path):
    phase, start, end = window(now,calendar)
    with closing(sqlite3.connect(path,timeout=2)) as db, db:
        db.row_factory = sqlite3.Row
        db.executescript(SCHEMA + friday_replay.SCHEMA)
        db.execute('BEGIN IMMEDIATE')
        quotes = {}
        details = {}
        def report(reason):
            if phase != 'regular_session':
                friday_replay.record(db,phase,start,end,now,batch,quotes,details,reason)
            db.execute('INSERT OR REPLACE INTO phase_health VALUES(1,?,?,?)',(phase,reason,now))
            return reason
        db.execute("UPDATE phase_probes SET state='unmeasured' WHERE state='open' AND due+15 < ?",(now,))
        if phase not in ('pre_market','after_hours'):
            return report(phase)
        if not batch or not 0 <= now-batch[0] <= 15:
            return report('waiting_for_fresh_scanner_batch')
        captured, snapshots = batch
        for symbol in sorted(COHORT.intersection(snapshots)):
            raw = snapshots[symbol]
            if not isinstance(raw,dict):
                continue
            quote = parse_snapshot(symbol,raw,feed='iex',now=now)
            if not (quote['trade_fresh'] and quote['quote_fresh']):
                continue
            qt = now-quote['quote_age_seconds']
            tt = now-quote['trade_age_seconds']
            if min(qt,tt) < max(start,now-15) or max(qt,tt) > captured:
                continue
            quotes[symbol] = (quote,qt)
            try:
                observed = bars([raw.get('minuteBar')],observed_at=captured)
            except (ValueError,TypeError,KeyError,OverflowError):
                continue
            if observed and observed[0]['v'] > 0 and start <= observed[0]['t'] and 0 <= now-observed[0]['t']-60 <= 75:
                candle = observed[0]
                db.execute('INSERT OR IGNORE INTO phase_bars VALUES(?,?,?)',(symbol,candle['t'],json.dumps(candle)))
        position = db.execute("SELECT * FROM phase_probes WHERE state='open' LIMIT 1").fetchone()
        if position:
            mark = quotes.get(position['symbol'])
            if position['phase'] != phase or not mark or not position['due'] <= mark[1] <= now:
                return report('awaiting_owned_forward_quote')
            bid = mark[0]['bid']
            # One share, crossed spread, 10 bps slippage and $0.005 per share on each side.
            net = bid*.999-position['ask']*1.001-.01
            db.execute("UPDATE phase_probes SET state='measured',exit_at=?,bid=?,net=? WHERE intent=?",
                       (mark[1],bid,net,position['intent']))
            return report('owned_probe_measured')
        db.execute('DELETE FROM phase_bars WHERE stamp < ?', (now-600,))
        if now+75 >= end:
            return report('insufficient_phase_time')
        if not quotes:
            return report('no_fresh_in_phase_quote_and_trade')
        reason = 'missing_contiguous_phase_chart'
        for symbol,(q,qt) in quotes.items():
            rows = [json.loads(r[0]) for r in db.execute('SELECT candle FROM phase_bars WHERE symbol=? AND stamp>=? ORDER BY stamp DESC LIMIT 3',(symbol,start))][::-1]
            details[symbol] = ('missing_contiguous_phase_chart',rows)
            if len(rows)!=3 or any(b['t']-a['t']!=60 for a,b in zip(rows,rows[1:])) or not 0 <= now-rows[-1]['t']-60 <= 30:
                continue
            a,b,c = rows
            if not (qt >= c['t']+60 and a['c'] < b['c'] < c['c'] and c['c'] > max(a['h'],b['h'])
                    and c['v'] >= max(a['v'],b['v']) and c['c'] <= q['ask'] <= c['c']*1.005
                    and (q['ask']/q['bid']-1) <= .0015 and q['ask'] <= 2000):
                details[symbol] = ('phase_momentum_or_liquidity_skip',rows)
                reason = 'phase_momentum_or_liquidity_skip'
                continue
            # One probe across the entire cohort per phase/day; immutable on replay.
            intent = 'friday-phase:'+datetime.fromtimestamp(now,EASTERN).date().isoformat()+':'+phase
            evidence = json.dumps({'bars':rows,'quote':q,'calendar':calendar,'feed':'iex'})
            inserted = db.execute("INSERT OR IGNORE INTO phase_probes VALUES(?,?,?,?,?,?,'open',NULL,NULL,NULL,?)",
                                  (intent,symbol,phase,now,q['ask'],now+60,evidence))
            reason = 'prospective_probe_opened' if inserted.rowcount else 'phase_daily_cap'
            details[symbol]=(reason,rows)
            for other in quotes.keys()-details.keys():
                details[other]=('not_evaluated_after_first_qualified_symbol',None)
            return report(reason)
        return report(reason)


def advance():
    return tick(_latest_batch,_calendar,now=time.time(),path=ledger_path())


def status():
    from friday_paper_boundary import preview
    path = ledger_path()
    empty = {'health':None,'probes':[],'replays':[]}
    if not path.exists():
        return empty
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='phase_health'").fetchone():
            return empty
        health = db.execute('SELECT phase,reason,checked FROM phase_health WHERE id=1').fetchone()
        rows = db.execute('SELECT intent,symbol,phase,opened,due,state,net,ask,bid FROM phase_probes ORDER BY opened DESC LIMIT 20')
        return {'replays':friday_replay.summary(db),'health':dict(health) if health else None,'probes':[{**dict(r),'entry_preview':preview(r,'buy'),
                'exit_preview':preview(r,'sell') if r['state']=='measured' else None} for r in rows]}

def regular_replay(db, now, reason, decisions):
    phase,start,end=window(now,_calendar)
    batch=_latest_batch
    if phase!='regular_session':
        return
    if not batch:
        friday_replay.record(db,phase,start,end,now,None,{},decisions,reason)
        return
    scanned,snapshots=batch
    quotes={}
    if 0 <= now-scanned <= 15:
        for symbol in COHORT.intersection(snapshots):
            q=parse_snapshot(symbol,snapshots[symbol],feed='iex',now=now)
            if q['quote_fresh'] and q['trade_fresh']:
                qt,tt=now-q['quote_age_seconds'],now-q['trade_age_seconds']
                if max(start,now-15) <= min(qt,tt) and max(qt,tt)<=scanned:
                    quotes[symbol]=q,qt
    friday_replay.record(db,phase,start,end,now,batch,quotes,decisions,reason)
