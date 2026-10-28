"""Incremental provider-ranked discovery; no richest-wallet substitution."""
WINDOWS = ('1W', '30d', '90d')


def advance(state, provider, now, choose_source, clock=None):
    discovery = state.setdefault('discovery', {'window':0, 'offset':0, 'pending':[], 'checked':0, 'last_fetch':0})
    if not discovery['pending'] and now-discovery['last_fetch'] >= 60:
        window = WINDOWS[discovery['window']]
        data = provider.request('/trader/gainers-losers', params={
            'type':window, 'sort_by':'realized_pnl', 'min_realized_pnl':1000,
            'offset':discovery['offset'], 'limit':100})
        rows = data.get('items') if isinstance(data,dict) else None
        if not isinstance(rows,list) or len(rows)>100:
            raise ValueError('Unsupported profitable-trader ranking response.')
        discovery['pending'] = [{**row, 'ranking_window':window} for row in rows if isinstance(row,dict) and row.get('network','solana')=='solana']
        discovery['last_fetch']=now
        if len(rows)<100 or discovery['offset']>=9900:
            discovery['offset']=0;discovery['window']=(discovery['window']+1)%len(WINDOWS)
        else:discovery['offset']+=100
    # Reserve provider capacity for forward swap polling and meme metadata.
    for _ in range(min(2,len(discovery['pending']))):
        row=discovery['pending'][0]
        result=provider.qualify(row)  # On provider failure keep this candidate queued.
        discovery['pending'].pop(0);discovery['checked']+=1
        enrolled=clock() if clock else now
        address=row.get('address')
        existing=next((r for r in state['traders'] if r['wallet']==address),None)
        if existing:
            if result and not existing.get('active',True):existing['buy_resume_at']=enrolled
            if result and not existing.get('active',True) and not any(p['wallet']==address for p in state['positions'].values()):
                state['cursors'][address]={'at':enrolled,'provider':existing.get('trade_provider','birdeye')}
            existing['active']=result is not None
            existing['reviewed_at']=now
            if result:existing.update(result)
        elif result:
            source=choose_source()
            state['traders'].append({**result,'active':True,'selected_at':enrolled,'trade_provider':source})
            state['cursors'][result['wallet']]={'at':enrolled,'provider':source}
