"""Offline aggregate-only retrospective on a single-account Alpaca PAPER SQLite export."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')


def stamp(value):
    value = datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone required')
    return value.astimezone(timezone.utc)


def positive(value):
    value = Decimal(str(value))
    if not value.is_finite() or value <= 0:
        raise ValueError('Positive finite quantity required')
    return value


def phase(when, calendar):
    local = when.astimezone(ET)
    session = calendar.get(local.date().isoformat())
    if not session:
        return 'unverified'
    minutes = local.strftime('%H:%M')
    opened, closed = session.get('open'), session.get('close')
    if opened != '09:30' or closed not in ('13:00','16:00'):
        return 'unverified'
    if '04:00' <= minutes < opened:
        return 'premarket'
    if opened <= minutes < closed:
        return 'regular'
    if closed == '16:00' and closed <= minutes < '20:00':
        return 'after_hours'
    return 'unverified'


def summarize(rows):
    def agg(group):
        n = len(group)
        gross = sum(r['gross'] for r in group)
        fees = sum(r['qty']*Decimal('.01') for r in group)
        turnover = sum(r['turnover'] for r in group)
        return {'trades':n,'fill_pnl':round(float(gross),2),
                'modeled_net_usd':{str(b):round(float(gross-fees-turnover*b/10000),2) for b in (0,10,30)}}
    dates = sorted({r['entry'].astimezone(ET).date() for r in rows})
    cutoff = dates[min(len(dates)-1,len(dates)*7//10)] if len(dates)>=2 else None
    groups = defaultdict(list)
    overlap = 0
    for r in rows:
        groups['all'].append(r)
        for k in ('symbol','provenance','phase'):
            groups[k+':'+r[k]].append(r)
        if cutoff:
            if r['exit'].astimezone(ET).date()<cutoff:
                groups['train'].append(r)
            elif r['entry'].astimezone(ET).date()>=cutoff:
                groups['holdout'].append(r)
            else:
                overlap += 1
    return {'chronological_cutoff':str(cutoff) if cutoff else None,'purged_overlapping_trades':overlap,
            'groups':{k:agg(v) for k,v in sorted(groups.items())}}


def analyze(path, calendar):
    counts = Counter()
    calendar = dict(calendar)
    fills = {}
    links = defaultdict(set)
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        counts['paper_orders_without_asset_class']=db.execute('''SELECT count(*) FROM broker_fills WHERE source='alpaca_paper' AND CASE WHEN json_valid(raw_order_json) THEN json_extract(raw_order_json,'$.asset_class') END IS NULL''').fetchone()[0]
        for r in db.execute("""SELECT order_id,symbol,side,shares,price,filled_at,raw_order_json
                               FROM broker_fills WHERE source='alpaca_paper'
                               AND CASE WHEN json_valid(raw_order_json) THEN json_extract(raw_order_json,'$.asset_class') END='us_equity'"""):
            counts['paper_equity_orders']+=1
            raw=json.loads(r['raw_order_json'])
            try:
                qty,price=positive(r['shares']),positive(r['price'])
                at=stamp(r['filled_at'])
                if (raw.get('id')!=r['order_id'] or raw.get('symbol')!=r['symbol'] or
                    str(raw.get('side')).upper()!=r['side'] or raw.get('status')!='filled' or
                    positive(raw['filled_qty'])!=qty or positive(raw['filled_avg_price'])!=price or
                    stamp(raw['filled_at'])!=at):
                    raise ValueError()
            except (ValueError,KeyError,InvalidOperation,TypeError):
                counts['unverified_fill_orders']+=1
                continue
            client=str(raw.get('client_order_id') or '').strip().lower()
            source='old_ai' if client.startswith('auto-entry-') else 'manual_or_untagged' if not client else 'unknown'
            fills[r['order_id']] = (r['symbol'],r['side'],qty,price,at,source)
            if r['side']=='BUY':
                for leg in (raw.get('legs') or []):
                    if isinstance(leg,dict) and leg.get('side')=='sell' and leg.get('id'):
                        links[leg['id']].add(r['order_id'])
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # Reuse only dated calendar evidence previously frozen by the app.
        if 'day_trader_experiments' in tables:
            observed = defaultdict(set)
            for (payload,) in db.execute('SELECT decision_json FROM day_trader_experiments'):
                try:
                    decision=json.loads(payload)
                    opened,closed=[datetime.fromtimestamp(decision[k],ET) for k in ('session_open','session_close')]
                    frozen=datetime.fromtimestamp(decision['observed_at'],ET)
                    if opened.date()==closed.date()==frozen.date() and opened <= frozen < closed:
                        observed[opened.date().isoformat()].add((opened.strftime('%H:%M'),closed.strftime('%H:%M')))
                except (TypeError,KeyError,ValueError,OverflowError,OSError):
                    continue
            for day,windows in observed.items():
                if day not in calendar and len(windows)==1:
                    opened,closed=next(iter(windows))
                    calendar[day]={'open':opened,'close':closed}
        if 'trade_book' in tables:
            for entry,exit_id in db.execute('SELECT entry_order_id,exit_order_id FROM trade_book WHERE exit_order_id IS NOT NULL'):
                if entry:
                    links[exit_id].add(entry)
            if 'trade_book_order_links' in tables:
                for entry,exit_id in db.execute("""SELECT t.entry_order_id,l.order_id FROM trade_book_order_links l
                    JOIN trade_book t ON t.id=l.trade_book_id WHERE upper(l.order_role)='EXIT'"""):
                    if entry:
                        links[exit_id].add(entry)
    owned=defaultdict(list)
    ambiguous=set()
    for ident,fill in fills.items():
        if fill[1]!='SELL':
            continue
        parents=links.get(ident,set())
        if len(parents)!=1:
            counts['ambiguous_or_unlinked_sells']+=1
            ambiguous.update(parents)
            continue
        owned[next(iter(parents))].append(fill)
    rows=[]
    for ident,(symbol,side,qty,buy,entered,origin) in fills.items():
        if side!='BUY':
            continue
        counts['entries_'+origin]+=1
        exits=owned[ident]
        if (ident in ambiguous or not exits or any(x[0]!=symbol or x[4]<entered for x in exits)
                or sum(x[2] for x in exits)!=qty):
            counts['open_incomplete_or_unlinked_entries']+=1
            continue
        proceeds=sum(x[2]*x[3] for x in exits)
        rows.append(dict(symbol=symbol,qty=qty,entry=entered,exit=max(x[4] for x in exits),
                         gross=proceeds-buy*qty,turnover=proceeds+buy*qty,phase=phase(entered,calendar),provenance=origin))
    times=[r[4] for r in fills.values()]
    return {'scope':'stock-only, full-paper-order fills; modeled fee $0.005/share/side plus incremental bps; actual fees unknown',
            'counts':dict(counts),'verified_fill_range':[min(times).isoformat(),max(times).isoformat()] if times else None,
            'verified_closed':len(rows),**summarize(rows)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paper_export',help='Private single-account stock-only PAPER broker_fills SQLite backup')
    parser.add_argument('--calendar',help='Optional JSON object keyed by exchange date with regular open and close')
    args=parser.parse_args()
    print(json.dumps(analyze(args.paper_export,json.loads(Path(args.calendar).read_text()) if args.calendar else {}),indent=2))
