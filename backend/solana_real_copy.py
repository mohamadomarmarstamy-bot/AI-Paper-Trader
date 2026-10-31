"""Locked live-copy preparation and non-transactional Phantom ownership proof."""
from contextlib import closing
from datetime import datetime,timezone
import base64
import hashlib
import json
import secrets
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
import database
from solana_copy_rpc import decode58
from solana_meme_bot import mint,Conflict

SCHEMA='''CREATE TABLE IF NOT EXISTS solana_real_copy_wallet(
 id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL DEFAULT 0,
 wallet_address TEXT NOT NULL DEFAULT '',verified_at REAL);
CREATE TABLE IF NOT EXISTS solana_real_copy_challenges(
 id TEXT PRIMARY KEY,wallet_address TEXT NOT NULL,origin TEXT NOT NULL,
 message TEXT NOT NULL,expires_at REAL NOT NULL,revision INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);'''


def wallet():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_real_copy_wallet'").fetchone()
        row=conn.execute('SELECT * FROM solana_real_copy_wallet WHERE id=1').fetchone() if exists else None
    return dict(row) if row else {'revision':0,'wallet_address':'','verified_at':None}


def initialize(conn):
    conn.executescript(SCHEMA)
    conn.execute('INSERT OR IGNORE INTO solana_real_copy_wallet(id) VALUES(1)');conn.commit()


def public():
    import solana_copy_trader as paper
    import solana_real_budget as budget
    import solana_real_worker as live
    import solana_real_execution as execution
    source=paper.public();saved=wallet()
    # One canonical source: PAPER enabled/cash/holdings can NEVER arm live execution.
    rules={k:v for k,v in source['config'].items() if k not in ('enabled','paper_capital_usd')}
    roster=[{k:v for k,v in r.items() if k not in ('research_evidence','public_research_evidence')} for r in source['traders']]
    blueprint={'settings':rules,'entry_protection':source['entry_protection'],'traders':roster}
    digest=hashlib.sha256(json.dumps(blueprint,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    now=time.time();feed=source.get('operational_health');runtime=live.public();flags=execution.configuration(saved['wallet_address']);settings=budget.settings()
    configured=all(flags[k] for k in ('release_allowed','signer_configured','signer_matches','jupiter_configured'))
    sized=bool(settings['config'].get('buy_usd' if settings['config']['buy_mode']=='fixed_usd' else 'buy_percent'))
    can_start=bool(configured and sized and saved['verified_at'] and source['config']['enabled'] and feed and feed['level']=='ok' and not runtime['enabled'])
    active=runtime['enabled'];held=runtime['positions'];stale=any(p.get('exit_value_lamports') is None or now-(p.get('marked_at') or 0)>60 for p in held)
    checks=[{'name':'Wallet ownership','level':'ok' if saved['wallet_address'] and saved['verified_at'] else 'warning',
             'detail':'Verified public wallet' if saved['wallet_address'] and saved['verified_at'] else 'Link and verify Phantom'},
            {'name':'Source monitoring','level':feed['level'] if feed else 'warning',
             'detail':feed['detail'] if feed else 'Source health is unavailable'},
            {'name':'Live executor','level':'ok' if flags['release_allowed'] else 'warning','detail':'Installed · server switch on' if flags['release_allowed'] else 'Installed · server switch off'},
            {'name':'Unattended signer','level':'ok' if flags['signer_matches'] else 'warning','detail':'Matches linked wallet' if flags['signer_matches'] else 'Trading signer is missing or does not match the linked wallet'},
            {'name':'Swap API','level':'ok' if flags['jupiter_configured'] else 'warning','detail':'Jupiter API key configured' if flags['jupiter_configured'] else 'Jupiter API key needed'},
            {'name':'Live worker','level':'error' if (active or held) and now-(runtime['last_cycle'] or 0)>60 else 'ok' if active or held else 'paused','detail':'Updated recently' if runtime['last_cycle'] and now-runtime['last_cycle']<=60 else 'Waiting for activation' if not active and not held else 'No recent live cycle'}]
    level='ok' if active else 'paused' if can_start else 'warning';label='Copying live' if active else 'Ready to start' if can_start else 'Setup required'
    detail='Monitoring real holdings and fresh admitted copy signals.' if active else 'Real trades are off until you activate copying.'
    if active or held or runtime['pending_count']:
        if active and runtime['last_cycle'] is None and now-(runtime['started_at'] or 0)<=15:level='warning';label='Starting live worker';detail='Waiting for the first live monitoring cycle.'
        elif now-(runtime['last_cycle'] or 0)>60:level='error';label='Live worker delayed';detail='Live monitoring needs a fresh cycle.'
        elif not configured:level='error';label='Live signing unavailable';detail='Held assets may not be protected until server signing and swap access are restored.'
        elif stale:level='error';label='Exit quote unavailable';detail='Some holdings do not have a current executable exit quote.'
        elif runtime['error']:level='warning';label='Live check needed';detail=runtime['error']
        elif runtime['pending_count']:level='warning';label='Confirming transaction';detail='New orders wait for the stored transaction to finalize.'
        elif not active:level='paused';label='Buys paused';detail='Existing live holdings remain monitored for exits.'
        elif feed and feed['level']!='ok':level='warning';label='Source feed delayed';detail='New buys wait for current source data; live exits are monitored separately.'
    health={'level':level,'label':label,'detail':detail,
            'checks':checks,'checked_at':now,'valid_until':feed.get('valid_until',now) if feed else now,
            'basis':'Live readiness and monitoring checks, not guaranteed execution or profit. Source signals come from the paper copier.'}
    unrealized=None if stale else sum(p['exit_value_lamports']-p['cost_lamports'] for p in held)/10**9
    return {'live_execution_enabled':active,'can_start':can_start,'lock_reason':None if configured else 'Complete server signer, Jupiter API and release-switch setup.',
            'server_time':now,'operational_health':health,'buy_settings':settings,'execution_setup':flags,'live_runtime':runtime,
            'wallet':{'address':saved['wallet_address'],'verified_at':saved['verified_at'],'revision':saved['revision'],
                      'ownership_verified':bool(saved['wallet_address'] and saved['verified_at']), 'spending_authorized':bool(configured and (active or held or runtime['pending_count']))},
            'live_positions':held,'live_trade_count':runtime['confirmed_trade_count'],'live_pnl_usd':None,
            'live_pnl_sol':runtime['realized_sol']+unrealized if unrealized is not None else None,
            'paper_sync':{'snapshot':digest,'settings_revision':source['revision'],'read_at':time.time(),
                'source_last_cycle':source['last_cycle'],'source_health':source['health'],'source_error':source['error'],
                'operational_health':feed,
                'blueprint':blueprint,'discovery':source.get('public_discovery',{}),
                'performance':{k:source[k] for k in ('closed_count','realized_pnl_usd','closed_return_percent','wins')},
                'signals':source['events'][:6],
                'learning_basis':'Current copy rules, researched wallets, discovery and recorded PAPER results. No separate adaptive copy-strategy learner is implemented.'}}


def challenge(address,revision,origin,now=None):
    from solana_real_worker import guard_wallet_change
    guard_wallet_change()
    now=time.time() if now is None else now
    address=mint(address)
    if len(decode58(address))!=32 or type(revision) is not int:raise ValueError('Valid public wallet and revision required.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        saved=conn.execute('SELECT * FROM solana_real_copy_wallet WHERE id=1').fetchone()
        if saved['revision']!=revision:raise Conflict('Wallet changed. Refresh before linking.')
        ident=secrets.token_urlsafe(32);expiry=now+300
        message='\n'.join(['AI Paper Trader · Real Copy Trade',f'Origin: {origin}',f'Wallet: {address}',
            'Link this public wallet. Live trading remains DISABLED.',
            'This signature does not authorize trades, transfers, spending or delegation.',
            f'Nonce: {ident}',f'Expires: {datetime.fromtimestamp(expiry,timezone.utc).isoformat()}'])
        conn.execute('DELETE FROM solana_real_copy_challenges WHERE expires_at<? OR used=1',(now,))
        # Bound storage; supersede this owner\'s outstanding signing prompts.
        conn.execute('DELETE FROM solana_real_copy_challenges')
        conn.execute('INSERT INTO solana_real_copy_challenges VALUES(?,?,?,?,?,?,0)',(ident,address,origin,message,expiry,revision));conn.commit()
    return {'challenge_id':ident,'message':message,'expires_at':expiry,'wallet_address':address,'live_execution_enabled':False}


def verify(ident,signature,origin,now=None):
    from solana_real_worker import guard_wallet_change
    guard_wallet_change()
    now=time.time() if now is None else now
    if not isinstance(ident,str) or not 20<=len(ident)<=100 or not isinstance(signature,str) or len(signature)>100:raise ValueError('Signed wallet challenge required.')
    try:raw=base64.b64decode(signature,validate=True)
    except (ValueError,TypeError):raise ValueError('Invalid message signature.') from None
    if len(raw)!=64:raise ValueError('Invalid message signature.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM solana_real_copy_challenges WHERE id=?',(ident,)).fetchone()
        if not row or row['used'] or row['origin']!=origin or now>=row['expires_at']:raise ValueError('Challenge expired or unavailable. Connect again.')
        try:Ed25519PublicKey.from_public_bytes(decode58(row['wallet_address'])).verify(raw,row['message'].encode('utf-8'))
        except (InvalidSignature,ValueError):raise ValueError('Wallet ownership signature did not match.') from None
        updated=conn.execute('UPDATE solana_real_copy_wallet SET wallet_address=?,verified_at=?,revision=revision+1 WHERE id=1 AND revision=?',(row['wallet_address'],now,row['revision']))
        if updated.rowcount!=1:raise Conflict('Wallet changed. Connect again.')
        conn.execute('UPDATE solana_real_copy_challenges SET used=1 WHERE id=?',(ident,));conn.commit()
    return wallet()


def unlink(revision):
    from solana_real_worker import guard_wallet_change
    guard_wallet_change()
    if type(revision) is not int:raise ValueError('Wallet revision required.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        if conn.execute("UPDATE solana_real_copy_wallet SET wallet_address='',verified_at=NULL,revision=revision+1 WHERE id=1 AND revision=?",(revision,)).rowcount!=1:raise Conflict('Wallet changed. Refresh before unlinking.')
        conn.execute('DELETE FROM solana_real_copy_challenges');conn.commit()
    return wallet()
