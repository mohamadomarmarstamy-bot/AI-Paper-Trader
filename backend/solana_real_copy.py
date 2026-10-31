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
    source=paper.public();saved=wallet()
    # One canonical source: PAPER enabled/cash/holdings can NEVER arm live execution.
    rules={k:v for k,v in source['config'].items() if k not in ('enabled','paper_capital_usd')}
    roster=[{k:v for k,v in r.items() if k not in ('research_evidence','public_research_evidence')} for r in source['traders']]
    blueprint={'settings':rules,'entry_protection':source['entry_protection'],'traders':roster}
    digest=hashlib.sha256(json.dumps(blueprint,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    now=time.time();feed=source.get('operational_health')
    checks=[{'name':'Wallet ownership','level':'ok' if saved['wallet_address'] and saved['verified_at'] else 'warning',
             'detail':'Verified public wallet' if saved['wallet_address'] and saved['verified_at'] else 'Link and verify Phantom'},
            {'name':'Source monitoring','level':feed['level'] if feed else 'warning',
             'detail':feed['detail'] if feed else 'Source health is unavailable'},
            {'name':'Live executor','level':'error','detail':'Not implemented · no real orders can be sent'},
            {'name':'Unattended signer','level':'warning','detail':'Not configured · wallet linking grants no spending authority'}]
    health={'level':'warning','label':'Setup required','detail':'Live copying is off. The executor and unattended signer are not configured.',
            'checks':checks,'checked_at':now,'valid_until':feed.get('valid_until',now) if feed else now,
            'basis':'Live readiness checks; source monitoring comes from the paper copier. No live protection or execution is active.'}
    return {'live_execution_enabled':False,'can_start':False,'lock_reason':'Live execution is not implemented in this release.',
            'server_time':now,'operational_health':health,
            'wallet':{'address':saved['wallet_address'],'verified_at':saved['verified_at'],'revision':saved['revision'],
                      'ownership_verified':bool(saved['wallet_address'] and saved['verified_at']), 'spending_authorized':False},
            'live_positions':[],'live_trade_count':0,'live_pnl_usd':None,
            'paper_sync':{'snapshot':digest,'settings_revision':source['revision'],'read_at':time.time(),
                'source_last_cycle':source['last_cycle'],'source_health':source['health'],'source_error':source['error'],
                'operational_health':feed,
                'blueprint':blueprint,'discovery':source.get('public_discovery',{}),
                'performance':{k:source[k] for k in ('closed_count','realized_pnl_usd','closed_return_percent','wins')},
                'signals':source['events'][:6],
                'learning_basis':'Current copy rules, researched wallets, discovery and recorded PAPER results. No separate adaptive copy-strategy learner is implemented.'}}


def challenge(address,revision,origin,now=None):
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
    if type(revision) is not int:raise ValueError('Wallet revision required.')
    with closing(database.get_connection()) as conn:
        initialize(conn);conn.execute('BEGIN IMMEDIATE')
        if conn.execute("UPDATE solana_real_copy_wallet SET wallet_address='',verified_at=NULL,revision=revision+1 WHERE id=1 AND revision=?",(revision,)).rowcount!=1:raise Conflict('Wallet changed. Refresh before unlinking.')
        conn.execute('DELETE FROM solana_real_copy_challenges');conn.commit()
    return wallet()
