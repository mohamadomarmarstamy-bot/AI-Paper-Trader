"""Shadow-only replay of the first 100 prospective Jarvis paper entries."""
import json

LIMIT = 100
FRESH_SECONDS = 15
UNAVAILABLE = {'no_route', 'partial_route', 'liquidity', 'below_fee'}


def queued():
    return {'samples':{}, 'quotes':[], 'signals':[], 'proposal':None}


def observe_quote(study, mint, size, now, kind, divergent=False):
    # This hook only receives existing mint -> USDC ExactIn jobs. Size is raw token units.
    quotes = [q for q in study['quotes'] if 0 <= now-q['at'] <= FRESH_SECONDS
              and (q['mint'],q['size']) != (mint,size)]
    quotes.append({'mint':mint,'size':size,'at':now,'kind':kind})
    study['quotes'] = quotes[-64:]
    if study['proposal'] or not (kind=='no_route' or divergent):
        return
    reason = 'no_route' if kind=='no_route' else 'chart_divergence'
    signals = [s for s in study['signals'] if 0 <= now-s['at'] <= 3600 and s['mint'] != mint]
    signals.append({'mint':mint,'at':now,'reason':reason})
    study['signals'] = signals[-3:]
    if len(signals)>=3:
        study['proposal']={'at':now,'reasons':[s['reason'] for s in signals]}


def diverges(anchor, market, size, amount, now):
    if not (anchor and market and anchor.get('pair')==market.get('pair')
            and anchor.get('quantity')==size and 0<=now-anchor.get('at',0)<=90
            and anchor.get('amount',0)>0 and anchor.get('price',0)>0):
        return False
    indicative=anchor['amount']*market['price_usd']/anchor['price']
    return indicative>0 and abs(amount-indicative)/indicative>=.20


def enter(study, p):
    samples=study['samples']
    if p['mint'] in samples or len(samples)>=LIMIT:
        return
    exact=next((q for q in reversed(study['quotes']) if q['mint']==p['mint']
                and q['size']==p['quantity'] and 0<p['opened']-q['at']<=FRESH_SECONDS),None)
    kind=exact['kind'] if exact else 'missing'
    decision='pass' if kind=='usable' else 'blocked' if kind in UNAVAILABLE else 'unknown'
    samples[p['mint']]={'pair':p['pair'],'opened':p['opened'],'size':p['quantity'],
        'entry_cost':p['entry_cost'],'policy':p['policy'],'decision':decision,'evidence':kind,
        'quote_at':exact['at'] if exact else None,'failed_exits':0,'no_route':0,'pnl':None}


def exit_failure(study, bot, p, category):
    sample=study['samples'].get(p['mint'])
    if bot=='jarvis' and sample and sample['opened']==p['opened']:
        sample['failed_exits']+=1
        sample['no_route']+=int(category=='no_route')


def settle(study, trades):
    for trade in trades:
        sample=study['samples'].get(trade['mint'])
        if trade['bot']=='jarvis' and sample and sample['opened']==trade['opened']:
            sample.update(pnl=trade['pnl'],closed=trade['closed'])


def report(conn):
    row=conn.execute('SELECT state FROM run WHERE id=1').fetchone()
    study=json.loads(row[0]).get('sell_quote_study',queued()) if row else queued()
    samples=list(study['samples'].values())
    matched=[s for s in samples if s['decision']!='unknown']
    closed=sorted((s for s in matched if s['pnl'] is not None),key=lambda s:(s['closed'],s['opened']))
    arms={}
    for name in ('baseline','challenger'):
        equity=peak=drawdown=0
        participated=[s for s in matched if name=='baseline' or s['decision']=='pass']
        for s in closed:
            equity+=s['pnl'] if name=='baseline' or s['decision']=='pass' else 0
            peak=max(peak,equity)
            drawdown=max(drawdown,peak-equity)
        arms[name]={'net_usd':equity/1e6 if closed else None,
            'realized_drawdown_usd':drawdown/1e6 if closed else None,
            'failed_exit_attempts':sum(s['failed_exits'] for s in participated),
            'no_route_attempts':sum(s['no_route'] for s in participated)}
    return {'status':'queued' if not samples else 'collecting' if len(samples)<LIMIT else
            'awaiting_closes' if any(s['pnl'] is None for s in samples) else 'ready_for_review',
            'limit':LIMIT,'sampled':len(samples),'matched':len(matched),'matched_closed':len(closed),
            'unknown':len(samples)-len(matched),'missing':sum(s['evidence']=='missing' for s in samples),
            'unavailable':sum(s['decision']=='blocked' for s in samples),
            'blocked_winners':sum(s['decision']=='blocked' and s['pnl']>0 for s in closed),
            'arms':arms,'proposal':study['proposal']}
