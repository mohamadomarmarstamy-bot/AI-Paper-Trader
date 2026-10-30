"""Read-only cohorts of actual fully closed automatic PAPER stock trades."""
from collections import defaultdict
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo
import time
from stock_momentum import number

ET=ZoneInfo('America/New_York')


def stamp(value):
    numeric=number(value)
    if numeric is not None and numeric>0:return numeric
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError,TypeError,OverflowError):return None


def metrics(rows):
    valid=[r for r in rows if r['net_pnl'] is not None]
    winners=[r for r in valid if r['net_pnl']>0];losers=[r for r in valid if r['net_pnl']<0]
    gain=sum(r['net_pnl'] for r in winners);loss=-sum(r['net_pnl'] for r in losers)
    return {'trades':len(rows),'costed_trades':len(valid),'wins':len(winners),'losses':len(losers),
        'symbols':len({r['symbol'] for r in rows}),'gross_pnl_usd':sum(r['gross_pnl'] for r in rows),
        'estimated_net_pnl_usd':sum(r['net_pnl'] for r in valid) if valid else None,
        'win_rate_percent':len(winners)/len(valid)*100 if valid else None,
        'expectancy_usd':mean(r['net_pnl'] for r in valid) if valid else None,
        'mean_net_return_percent':mean(r['net_return'] for r in valid) if valid else None,
        'profit_factor':gain/loss if loss else None,'no_net_losses':bool(valid) and not losers,
        'sample_note':'Small sample: fewer than 30 costed trades.' if len(valid)<30 else 'Observational results; not proof of an edge.'}


def report(conn,config,*,days=90,now=None):
    import json
    clock=now if now is not None else time.time()
    required=('trade_book','learning_outcomes','stock_entry_evidence')
    exists=all(conn.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone() for name in required)
    records=[];excluded_dates=0
    if exists:
        columns={r['name'] for r in conn.execute('PRAGMA table_info(trade_book)')}
        entry='t.entry_timestamp' if 'entry_timestamp' in columns else 'NULL'
        exit='t.exit_timestamp' if 'exit_timestamp' in columns else 'NULL'
        rows=conn.execute(f'''SELECT lo.id,lo.symbol,lo.entry_price,lo.shares,lo.realized_profit_loss,lo.realized_return_percent,
            {entry} AS entered_at,{exit} AS exited_at,e.features_json
            FROM learning_outcomes lo JOIN trade_book t ON t.id=lo.trade_book_id
            LEFT JOIN stock_entry_evidence e ON e.order_id=t.entry_order_id
            WHERE t.status='CLOSED' AND t.entry_reason='auto_trader_entry' ORDER BY lo.id DESC LIMIT 5000''').fetchall()
        cost=2*(config['stock_slippage_bps_per_side']+config['stock_fee_bps_per_side'])/100
        for row in rows:
            end=stamp(row['exited_at'])
            if days and (end is None or not clock-days*86400<=end<=clock):
                if end is None:excluded_dates+=1
                continue
            if end is not None and end>clock:continue
            features=json.loads(row['features_json']) if row['features_json'] else {}
            entered=stamp(row['entered_at']) or stamp(features.get('captured_at'))
            local=datetime.fromtimestamp(entered,ET) if entered is not None else None
            minute=local.hour*60+local.minute if local else None
            period='Unknown entry time' if minute is None else 'Opening hour · 9:30–10:30 ET' if 570<=minute<630 else 'Late morning · 10:30–12 ET' if 630<=minute<720 else 'Afternoon · 12–16 ET' if 720<=minute<960 else 'Outside regular hours'
            shares=number(row['shares']);price=number(row['entry_price']);gross=number(row['realized_profit_loss']);ret=number(row['realized_return_percent'])
            if gross is None:continue
            notional=price*shares if price and shares and price>0 and shares>0 else None
            floating=number(features.get('float_shares'))
            float_group='Unknown float' if floating is None or floating<=0 else 'Under 5M shares' if floating<5e6 else '5M–10M shares' if floating<10e6 else '10M–20M shares' if floating<=20e6 else 'Over 20M shares'
            setup={'breakout':'Breakout','vwap_pullback':'VWAP pullback'}.get(features.get('entry_setup'),'Unknown / legacy setup')
            records.append({'symbol':row['symbol'],'gross_pnl':gross,'net_pnl':gross-notional*cost/100 if notional is not None and ret is not None else None,
                'net_return':ret-cost if notional is not None and ret is not None else None,'setup':setup,'session':period,'float':float_group})
    groups={}
    for dimension in ('setup','session','float'):
        cohorts=defaultdict(list)
        for row in records:cohorts[row[dimension]].append(row)
        groups[dimension]=[{'label':label,**metrics(values)} for label,values in sorted(cohorts.items())]
    return {'summary':metrics(records),'groups':groups,'window_days':days,'as_of':clock,
        'coverage':{'known_setup':sum(r['setup']!='Unknown / legacy setup' for r in records),
                    'known_time':sum(r['session']!='Unknown entry time' for r in records),
                    'known_float':sum(r['float']!='Unknown float' for r in records),'missing_exit_dates_excluded':excluded_dates},
        'cost_model':{k:config[k] for k in ('stock_slippage_bps_per_side','stock_fee_bps_per_side')},
        'basis':'Actual fully closed broad automatic PAPER stock trades, up to the latest 5,000 records. Net estimates reprice this window using the displayed current extra cost assumptions. Unknown entry evidence stays in explicit legacy/unknown cohorts. Manual Pro Ticker and meme trades are excluded. Break-even trades are included in the win-rate denominator; profit factor is unavailable with no losses.'}
