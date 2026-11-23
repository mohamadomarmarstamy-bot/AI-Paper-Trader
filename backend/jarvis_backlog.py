"""Paper-only backlog alerts and expiring, single-use pause links."""
import asyncio
import hashlib
import html
import json
import logging
import os
import secrets
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

import solana_http

DEFAULTS = {'enabled': True, 'threshold': 100}
DURATIONS = (10, 30, 60)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS backlog_guard(id INTEGER PRIMARY KEY CHECK(id=1), episode TEXT, low_since REAL);
INSERT OR IGNORE INTO backlog_guard(id) VALUES(1);
CREATE TABLE IF NOT EXISTS backlog_mail(
 id TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, payload TEXT NOT NULL,
 sent REAL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
 lease REAL NOT NULL DEFAULT 0, consumed REAL, duration INTEGER);
CREATE TABLE IF NOT EXISTS backlog_action(
 digest TEXT PRIMARY KEY, mail_id TEXT NOT NULL, minutes INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS backlog_pause_report(
 id TEXT PRIMARY KEY, started REAL NOT NULL, ends REAL NOT NULL, baseline_id INTEGER NOT NULL,
 cancelled REAL, queued REAL);
'''


def api():
    import jarvis_test_bot
    return jarvis_test_bot


def waiting(state, now):
    positions = [p for b in state['bots'].values() for p in b['positions'].values()
                 if p['due'] <= now or p.get('mark_stale') or p.get('pending')]
    return {'positions': len(positions), 'coins': len({p['mint'] for p in positions})}


def summary(state, now):
    settings = dict(DEFAULTS, **state.get('backlog_settings', {}))
    return {**settings, **waiting(state, now), 'pause_until': state.get('entry_pause_until', 0),
            'timer_active': state.get('entry_pause_until', 0) > now}


def timer_active(state, now):
    return state.get('entry_pause_until', 0) > now


def save_state(conn, state, revision):
    conn.execute('UPDATE run SET state=?,revision=? WHERE id=1', (json.dumps(state), revision + 1))


def configure(changes):
    if (not isinstance(changes, dict) or set(changes) != set(DEFAULTS)
            or type(changes['enabled']) is not bool or type(changes['threshold']) is not int
            or not 1 <= changes['threshold'] <= 10000):
        raise ValueError('Choose an alert threshold from 1 to 10000 positions.')
    jarvis = api()
    with jarvis.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, revision = jarvis.read(conn)
        state['backlog_settings'] = changes
        save_state(conn, state, revision)
    return jarvis.status()


def apply_control(state, minutes, now):
    if type(minutes) is not int or minutes not in (0, *DURATIONS):
        raise ValueError('Choose 10, 30 or 60 minutes, or resume.')
    if minutes:
        state['entry_pause_until'] = now + minutes * 60
    else:
        state['entry_pause_until'] = 0
        pause = state.setdefault('entry_catchup_pause', {})
        pause.update(request=os.getenv('JARVIS_ENTRY_CATCHUP_REQUEST', '').strip(),
                     active=False, resumed_at=now, manual=True)
        # Resume entry permissions, but honor the owner's overall run toggle.


def record_pause(conn, state, minutes, now):
    # Replacing or manually ending a live timer cancels that timer's expiry report.
    conn.execute('UPDATE backlog_pause_report SET cancelled=? WHERE queued IS NULL AND cancelled IS NULL AND ends>?', (now, now))
    if minutes:
        baseline = conn.execute('SELECT COALESCE(max(id),0) FROM trades').fetchone()[0]
        conn.execute('INSERT INTO backlog_pause_report(id,started,ends,baseline_id) VALUES(?,?,?,?)',
                     (secrets.token_hex(16), now, state['entry_pause_until'], baseline))


def enqueue_expired(conn, state, now, base):
    for report in conn.execute('SELECT * FROM backlog_pause_report WHERE queued IS NULL AND cancelled IS NULL AND ends<=? ORDER BY ends LIMIT 10', (now,)).fetchall():
        closed = conn.execute('SELECT count(*) FROM trades WHERE id>? AND closed>=? AND closed<=?',
                              (report['baseline_id'], report['started'], report['ends'])).fetchone()[0]
        pending = waiting(state, now)
        running = state['enabled'] and not timer_active(state, now) and not api().catchup_requested(state)
        status = ('Paper buys are back up and running.' if running else
                  'Your buy-pause timer has ended, but paper buys remain paused by another control (catch-up pause, newer timer, or overall stop switch).')
        checked = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).strftime('%b %d, %Y %I:%M:%S %p ET')
        text = (f"{status} Status checked {checked}.\n{closed} Jarvis paper positions closed during this timer.\n"
                f"{pending['positions']} positions across {pending['coins']} distinct coins are still waiting for an exit quote check or route retry as of this report.\n"
                'Sells continue running.\nSettings: ' + base + '/jarvis-test-bot#settings')
        payload = {'subject': 'Jarvis paper: buys back up and running' if running else 'Jarvis paper: timer ended — buys still paused',
                   'text': text, 'html': '<h2>Paper buy-pause timer ended</h2><p>' + html.escape(text).replace('\n', '<br>') + '</p>'}
        conn.execute('INSERT OR IGNORE INTO backlog_mail(id,created,expires,payload) VALUES(?,?,?,?)',
                     ('resume/' + report['id'], now, now + 21600, json.dumps(payload)))
        conn.execute('UPDATE backlog_pause_report SET queued=? WHERE id=?', (now, report['id']))


def control(minutes, clock=time.time):
    jarvis = api()
    with jarvis.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, revision = jarvis.read(conn)
        now = clock()
        apply_control(state, minutes, now)
        record_pause(conn, state, minutes, now)
        save_state(conn, state, revision)
    return jarvis.status()


def origin():
    value = os.getenv('APP_PUBLIC_ORIGIN', '').strip().rstrip('/')
    if not value:
        domain = os.getenv('RAILWAY_PUBLIC_DOMAIN', '').strip()
        value = 'https://' + domain if domain else ''
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or parts.path or parts.query or parts.fragment or any(c.isspace() for c in value)):
        return None
    return value


def enqueue(conn, state, now, base):
    config = summary(state, now)
    guard = conn.execute('SELECT * FROM backlog_guard WHERE id=1').fetchone()
    if not config['enabled']:
        return
    if config['positions'] < max(1, config['threshold'] * .8):
        low = guard['low_since'] if guard['low_since'] is not None else now
        conn.execute('UPDATE backlog_guard SET low_since=? WHERE id=1', (low,))
        if now - low >= 60:
            conn.execute('UPDATE backlog_guard SET episode=NULL WHERE id=1')
        return
    conn.execute('UPDATE backlog_guard SET low_since=NULL WHERE id=1')
    if config['positions'] < config['threshold'] or guard['episode']:
        return
    ident = secrets.token_hex(16)
    links = []
    for minutes in DURATIONS:
        token = secrets.token_urlsafe(32)
        conn.execute('INSERT INTO backlog_action VALUES(?,?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), ident, minutes))
        # Fragments never reach proxy/access logs or referrer headers.
        links.append((minutes, base + '/jarvis-test-bot/email-pause#' + token))
    text = (f"{config['positions']} paper positions across {config['coins']} distinct coins are waiting for an exit quote check or route retry. "
            'This is not a count of confirmed sell orders. Sells keep running during buy pauses.\n'
            'Choose a temporary buy pause (links expire in six hours; one choice per alert):\n')
    text += '\n'.join(f'Pause {minutes} minutes: {url}' for minutes, url in links)
    text += '\nA longer catch-up pause or the overall stop switch can keep buys off after the timer expires.\n'
    text += 'Settings and Resume buys: ' + base + '/jarvis-test-bot#settings'
    buttons = ''.join(f'<p><a style="display:inline-block;padding:14px;background:#275bdd;color:white;border-radius:8px" href="{html.escape(url, quote=True)}">Pause buys for {minutes} minutes</a></p>' for minutes, url in links)
    body = f'<h2>Jarvis paper exit backlog</h2><p>{html.escape(text.split(chr(10))[0])}</p>{buttons}<p>Sells continue. Links expire in six hours. One choice per alert.</p><p>A longer catch-up pause or the overall stop switch can keep buys off after the timer expires.</p><a href="{base}/jarvis-test-bot#settings">Open settings / Resume buys</a>'
    payload = {'subject': f"Jarvis paper: {config['positions']} positions waiting for exit checks", 'text': text, 'html': body}
    conn.execute('INSERT INTO backlog_mail(id,created,expires,payload) VALUES(?,?,?,?)',
                 (ident, now, now + 21600, json.dumps(payload)))
    conn.execute('UPDATE backlog_guard SET episode=? WHERE id=1', (ident,))


def email_control(token, clock=time.time):
    if not isinstance(token, str) or len(token) != 43:
        raise ValueError('This pause link is invalid or expired. Open app settings.')
    now = clock()
    jarvis = api()
    with jarvis.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        action = conn.execute('SELECT a.minutes,m.id,m.expires,m.consumed,m.duration FROM backlog_action a JOIN backlog_mail m ON m.id=a.mail_id WHERE a.digest=?',
                              (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not action or action['expires'] <= now:
            raise ValueError('This pause link is invalid or expired. Open app settings.')
        if action['consumed'] is not None:
            return {'applied': False, 'minutes': action['duration'], 'message': 'This alert was already used. The pause was not extended.'}
        state, revision = jarvis.read(conn)
        apply_control(state, action['minutes'], now)
        record_pause(conn, state, action['minutes'], now)
        save_state(conn, state, revision)
        conn.execute('UPDATE backlog_mail SET consumed=?,duration=? WHERE id=?', (now, action['minutes'], action['id']))
        return {'applied': True, 'minutes': action['minutes'], 'pause_until': state['entry_pause_until'],
                'message': f"Paper buys paused for {action['minutes']} minutes. Sells keep running."}


def mail_tick(clock=time.time, post=solana_http.post):
    now = clock()
    base = origin()
    if not base or not all(os.getenv(k, '').strip() for k in ('RESEND_API_KEY', 'HEALTH_ALERT_EMAIL_FROM', 'HEALTH_ALERT_EMAIL_TO')):
        return
    jarvis = api()
    with jarvis.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state, _ = jarvis.read(conn)
        enqueue_expired(conn, state, now, base)
        enqueue(conn, state, now, base)
        row = conn.execute("SELECT * FROM backlog_mail WHERE sent IS NULL AND expires>? AND next_attempt<=? AND lease<=? AND (id LIKE 'resume/%' OR ?) ORDER BY created LIMIT 1", (now, now, now, int(summary(state, now)['enabled']))).fetchone()
        if row is None:
            return
        conn.execute('UPDATE backlog_mail SET lease=?,attempts=attempts+1 WHERE id=?', (now + 45, row['id']))
        payload = json.loads(row['payload'])
        payload.update({'from': os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(), 'to': [os.environ['HEALTH_ALERT_EMAIL_TO'].strip()]})
    success = False
    try:
        response = post('https://api.resend.com/emails', json=payload,
                        headers={'Authorization': 'Bearer ' + os.environ['RESEND_API_KEY'].strip(),
                                 'Idempotency-Key': 'jarvis-backlog/' + row['id']}, timeout=8, allow_redirects=False)
        success = response.status_code in (200, 201, 202)
    except Exception:
        pass  # Never echo a response body, token, or recipient into logs.
    with jarvis.connection() as conn, conn:
        conn.execute('UPDATE backlog_mail SET sent=?,lease=0,next_attempt=? WHERE id=?',
                     (clock() if success else None, clock() + min(3600, 60 * 2 ** min(row['attempts'], 6)), row['id']))


async def worker():
    while True:
        try:
            await asyncio.to_thread(mail_tick)
        except Exception:
            logging.getLogger(__name__).warning('Jarvis backlog alert check unavailable.')
        await asyncio.sleep(30)
