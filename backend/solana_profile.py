"""Owner profile and write-only provider credential, independent of PAPER accounts."""
from contextlib import closing
import os
from pathlib import Path
import stat

from cryptography.fernet import Fernet, InvalidToken
import database
from solana_meme_bot import Conflict, mint


class Unavailable(ValueError):
    pass


SCHEMA = '''CREATE TABLE IF NOT EXISTS solana_owner_profile (
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0,
 name TEXT NOT NULL DEFAULT '', wallet_address TEXT NOT NULL DEFAULT '',
 key_mode TEXT NOT NULL DEFAULT 'inherit', encrypted_key BLOB);'''


def _row():
    empty = {'revision': 0, 'name': '', 'wallet_address': '', 'key_mode': 'inherit', 'encrypted_key': None}
    with closing(database.get_connection()) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_owner_profile'").fetchone():
            return empty
        row = conn.execute('SELECT * FROM solana_owner_profile WHERE id=1').fetchone()
        return dict(row) if row else empty


def _cipher(create=False):
    # The encryption key lives beside the persistent DB, outside database exports.
    path = Path(str(database.DATABASE_PATH) + '.profile-key')
    if create and not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(Fernet.generate_key())
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
            raise Unavailable('Secure profile storage is unavailable.')
        return Fernet(stream.read(128))


def api_key():
    row = _row()
    if row['key_mode'] == 'inherit':
        return os.getenv('BIRDEYE_API_KEY', '').strip()
    if row['key_mode'] == 'removed':
        return ''
    try:
        return _cipher().decrypt(row['encrypted_key']).decode('utf-8')
    except (OSError, ValueError, InvalidToken, TypeError):
        raise Unavailable('Saved API key is unavailable. Replace it in Profile.') from None


def solscan_api_key():
    with closing(database.get_connection()) as conn:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='solana_solscan_credential'").fetchone()
        row=conn.execute('SELECT key_mode,encrypted_key FROM solana_solscan_credential WHERE id=1').fetchone() if exists else None
    if not row:return os.getenv('SOLSCAN_API_KEY','').strip()
    if row['key_mode']=='removed':return ''
    try:return _cipher().decrypt(row['encrypted_key']).decode('utf-8')
    except (OSError,ValueError,InvalidToken,TypeError):raise Unavailable('Saved Solscan key is unavailable. Replace it in Profile.') from None


def public():
    row = _row()
    try:
        configured = bool(api_key())
        error = None
    except Unavailable as exc:
        configured, error = False, str(exc)
    try:solscan_configured,solscan_error=bool(solscan_api_key()),None
    except Unavailable as exc:solscan_configured,solscan_error=False,str(exc)
    return {'name': row['name'], 'wallet_address': row['wallet_address'],
            'revision': row['revision'], 'api_key_configured': configured,
            'api_key_source': 'server' if row['key_mode'] == 'inherit' and configured else 'profile' if configured else None,
            'error': error,'solscan_api_key_configured':solscan_configured,'solscan_error':solscan_error}


def save(payload):
    required={'name','wallet_address','api_key','remove_api_key','expected_revision'}
    if not isinstance(payload,dict) or not required<=set(payload) or not set(payload)<=required|{'solscan_api_key','remove_solscan_api_key'}:
        raise ValueError('Profile fields and revision required.')
    name, wallet, key = payload['name'], payload['wallet_address'], payload['api_key']
    if not all(isinstance(v, str) for v in (name, wallet, key)):
        raise ValueError('Profile fields must be text.')
    name, wallet, key = name.strip(), wallet.strip(), key.strip()
    if len(name) > 100 or any(ord(c) < 32 for c in name):
        raise ValueError('Name must be at most 100 characters without control characters.')
    if wallet:
        try: mint(wallet)
        except ValueError: raise ValueError('Enter a valid Solana wallet address or leave it blank.') from None
    if key and (len(key) > 512 or any(not 33 <= ord(c) <= 126 for c in key)):
        raise ValueError('Enter an API key of at most 512 characters without spaces.')
    remove, revision = payload['remove_api_key'], payload['expected_revision']
    if type(remove) is not bool or type(revision) is not int or revision < 0 or (remove and key):
        raise ValueError('Choose either replacing or removing the API key, with a valid revision.')
    solscan=payload.get('solscan_api_key','');remove_solscan=payload.get('remove_solscan_api_key',False)
    if not isinstance(solscan,str) or type(remove_solscan) is not bool:raise ValueError('Invalid Solscan credential fields.')
    solscan=solscan.strip()
    if len(solscan)>512 or any(not 33<=ord(c)<=126 for c in solscan) or (solscan and remove_solscan):raise ValueError('Invalid Solscan API key.')
    with closing(database.get_connection()) as conn:
        conn.execute(SCHEMA)
        conn.execute("CREATE TABLE IF NOT EXISTS solana_solscan_credential(id INTEGER PRIMARY KEY CHECK(id=1),key_mode TEXT NOT NULL,encrypted_key BLOB)")
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT OR IGNORE INTO solana_owner_profile(id) VALUES(1)')
        row = conn.execute('SELECT * FROM solana_owner_profile WHERE id=1').fetchone()
        if row['revision'] != revision:
            conn.rollback()
            raise Conflict('Profile changed in another tab. Reload before saving.')
        mode, encrypted = row['key_mode'], row['encrypted_key']
        if key:
            try: encrypted = _cipher(create=True).encrypt(key.encode('utf-8'))
            except (OSError, ValueError):
                raise Unavailable('Secure profile storage is unavailable. No profile changes saved.') from None
            mode = 'saved'
        elif remove:
            mode, encrypted = 'removed', None
        if solscan or remove_solscan:
            try:secret=_cipher(create=True).encrypt(solscan.encode()) if solscan else None
            except (OSError,ValueError):raise Unavailable('Secure profile storage unavailable; changes not saved.') from None
            conn.execute('INSERT OR REPLACE INTO solana_solscan_credential VALUES(1,?,?)',('saved' if solscan else 'removed',secret))
        conn.execute('UPDATE solana_owner_profile SET name=?,wallet_address=?,key_mode=?,encrypted_key=?,revision=revision+1 WHERE id=1',
                     (name, wallet, mode, encrypted))
        conn.commit()
    return public()
