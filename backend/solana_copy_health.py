"""Read-only operational health for the isolated PAPER copier."""
import math


def age(now,at):
    if not isinstance(at,(int,float)) or not math.isfinite(at) or at<=0 or at>now+5:return None
    return max(0,now-at)


def summary(state,now):
    config=state['config'];positions=list(state['positions'].values())
    active={r['wallet'] for r in state['traders'] if r.get('active',True)} if config['enabled'] else set()
    watched=active|{p['wallet'] for p in positions}
    cycle_age=age(now,state.get('last_cycle'));checks=[];deadlines=[]
    worker='ok' if cycle_age is not None and cycle_age<=60 else 'warning' if cycle_age is not None and cycle_age<=120 else 'error'
    checks.append({'name':'Bot heartbeat','level':worker,'detail':'Updated recently' if worker=='ok' else 'Cycle delayed' if worker=='warning' else 'No recent cycle'})
    if cycle_age is not None:deadlines.append(state['last_cycle']+60)
    stale=0;missing=0;backlog=0;source_errors=0;recent_sources=0
    for wallet in watched:
        cursor=state['cursors'].get(wallet,{})
        coverage=cursor.get('rpc_coverage') or {}
        checked=coverage.get('checked_at') if cursor.get('provider')=='rpc' else cursor.get('at') if wallet in state.get('last_polled',{}) else None
        elapsed=age(now,checked)
        if elapsed is not None and elapsed<=120:recent_sources+=1
        if elapsed is None:missing+=1
        elif elapsed>60:stale+=1
        else:deadlines.append(checked+60)
        if coverage.get('queued',0)>0 or coverage.get('history_truncated') or cursor.get('window'):backlog+=1
        if coverage.get('error'):source_errors+=1
    delayed=stale+missing
    sources='warning' if not watched or delayed or backlog or source_errors else 'ok'
    checks.append({'name':'Wallet feed','level':sources,'detail':f'{len(watched)-delayed}/{len(watched)} recently checked'+(f' · {backlog} catching up' if backlog else '') if watched else 'No wallets being watched'})
    unpriced=sum(p.get('mark_usd') is None or (age(now,p.get('marked_at')) is None or age(now,p.get('marked_at'))>120) for p in positions)
    for p in positions:
        if age(now,p.get('marked_at')) is not None:deadlines.append(p['marked_at']+120)
    checks.append({'name':'Holding prices','level':'error' if unpriced else 'ok','detail':f'{unpriced} waiting for prices' if unpriced else f'{len(positions)} priced' if positions else 'No open holdings'})
    stop_on=config.get('stop_loss_percent',0)>0
    checks.append({'name':'Loss protection','level':'ok' if stop_on else 'warning','detail':f"{config.get('stop_loss_percent',0):g}% stop · trigger, not a guaranteed fill" if stop_on else 'Loss stop is off'})
    level='ok';label='Watching';detail='Wallet data is current. Waiting for eligible trades.'
    if not config['enabled'] and not positions:
        level='paused';label='Paused';detail='New copied buys are paused.'
    elif state.get('last_cycle') is None and (age(now,state.get('inactivity_enabled_at')) or 0)<=60 and state.get('inactivity_enabled_at'):
        level='warning';label='Starting';detail='Waiting for the first completed cycle.'
    elif worker=='error':
        level='error';label='Bot not updating';detail='No recent trading cycle. Check diagnostics.'
    elif unpriced:
        level='error';label='Prices unavailable';detail='Some exits are waiting for a current price.'
    elif state.get('health')=='waiting_for_data_key':
        level='error';label='Feed not connected';detail='Connect a supported data source to copy trades.'
    elif watched and recent_sources==0 and (age(now,state.get('inactivity_enabled_at') or state.get('started_at')) or 0)>120:
        level='error';label='Wallet feed unavailable';detail='No watched wallet has a recent successful data check.'
    elif state.get('error') or source_errors:
        level='warning';label='Feed issue';detail='Wallet data has an error. Some trades may be missed.'
    elif worker=='warning':
        level='warning';label='Cycle delayed';detail='The trading loop is taking longer than expected.'
    elif backlog:
        level='warning';label='Catching up';detail=f'{backlog} wallet feed(s) have pending history.'
    elif delayed:
        level='warning';label='Wallet checks delayed';detail=f'{delayed} wallet feed(s) need a fresh successful check.'
    elif not watched:
        level='warning';label='Waiting for wallets';detail='No active source wallets are being watched.'
    elif not stop_on:
        level='warning';label='Loss stop off';detail='Copying can continue, but the loss stop is disabled.'
    elif not config['enabled']:
        level='paused';label='Buys paused';detail='Existing holdings are still monitored for exits.'
    return {'level':level,'label':label,'detail':detail,'checks':checks,'checked_at':now,
            'valid_until':min((d for d in deadlines if d>=now),default=now+10),
            'watched_wallets':len(watched),'delayed_wallets':delayed,'backlogged_wallets':backlog,
            'unpriced_holdings':unpriced,'basis':'Operational data checks only; not a guarantee of fills, protection or profit.'}
