"""One validated, revisioned settings service for the main app and Jarvis."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import uuid
import database

DEFAULTS = {'position_size_percent':2.0,'max_order_size_percent':2.5,
 'max_position_size_percent':5.0,'max_open_positions':50,'stop_loss_percent':2.0,
 'take_profit_percent':4.0,'profit_lock_trigger_percent':.75,'profit_lock_percent':.25,
 'trailing_stop_percent':1.0,'daily_profit_target_percent':5.0,'daily_loss_limit_percent':1.0}
RANGES = {'position_size_percent':(.1,100),'max_order_size_percent':(.1,100),
 'max_position_size_percent':(.1,100),'max_open_positions':(1,500),'stop_loss_percent':(.1,25),
 'take_profit_percent':(.1,100),'profit_lock_trigger_percent':(.1,100),'profit_lock_percent':(.01,50),
 'trailing_stop_percent':(.1,50),'daily_profit_target_percent':(.1,100),'daily_loss_limit_percent':(.1,100)}
PREFERENCES={'pause_new_entries':True,'notify_trade_events':True,
 'notify_protection_events':True,'notify_scanner_events':True}
ALL_DEFAULTS={**DEFAULTS,**PREFERENCES}
EFFECT='Applies to subsequent PAPER decisions. Existing orders/positions are unchanged; this does not enable an automation service.'
SCHEMA='''CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings_audit(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,
 actor TEXT NOT NULL,created_at TEXT NOT NULL,changes_json TEXT NOT NULL,before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,revision_after TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jarvis_requests(id TEXT PRIMARY KEY,message TEXT NOT NULL,state TEXT NOT NULL,
 created_at REAL NOT NULL,receipt_json TEXT,response_json TEXT);
CREATE TABLE IF NOT EXISTS jarvis_proposals(id TEXT PRIMARY KEY,created_at REAL NOT NULL,
 changes_json TEXT NOT NULL,revision TEXT NOT NULL,request_id TEXT NOT NULL);'''

class Conflict(ValueError):pass


def identity(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError('Canonical request ID required.')
    return value


def connect():
    conn=database.get_connection()
    conn.executescript(SCHEMA)
    return conn


def validate(changes):
    if not isinstance(changes,dict) or not changes or set(changes)-set(ALL_DEFAULTS):
        raise ValueError('Provide supported settings only.')
    result={}
    for key,value in changes.items():
        if key in PREFERENCES:
            if type(value) is not bool:raise ValueError(key+' must be a Boolean.')
            result[key]=value
        else:
            if type(value) not in (int,float):raise ValueError(key+' must be a finite number.')
            try:finite=math.isfinite(float(value))
            except OverflowError:finite=False
            if not finite:raise ValueError(key+' must be a finite number.')
            lo,hi=RANGES[key]
            if not lo<=value<=hi:raise ValueError(f'{key} must be between {lo} and {hi}.')
            if key=='max_open_positions' and int(value)!=value:raise ValueError('Maximum positions must be a whole number.')
            result[key]=int(value) if key=='max_open_positions' else float(value)
    return result


def _snapshot(conn):
    rows=[dict(row) for row in conn.execute('SELECT key,value,updated_at FROM app_settings ORDER BY key') if row['key'] in ALL_DEFAULTS]
    settings=dict(ALL_DEFAULTS)
    for row in rows:
        key=row['key'];raw=row['value']
        try:
            if key in PREFERENCES:
                if raw not in ('true','false'):raise ValueError('Invalid saved Boolean.')
                value=raw=='true'
            else:value=float(raw)
            settings.update(validate({key:value}))
        except (TypeError,ValueError):
            raise ValueError('Saved settings require repair; no update was applied.') from None
    revision=hashlib.sha256(json.dumps([rows,ALL_DEFAULTS],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'settings':settings,'revision':revision,'effect':EFFECT}


def receipt(row):
    return {'id':row['id'],'request_id':row['request_id'],'actor':row['actor'],'created_at':row['created_at'],
        'before':json.loads(row['before_json']),'after':json.loads(row['after_json']),
        'revision_after':row['revision_after'],'effect':EFFECT}


def snapshot():
    with closing(connect()) as conn:
        result=_snapshot(conn)
        result['audit']=[receipt(row) for row in conn.execute('SELECT * FROM app_settings_audit ORDER BY rowid DESC LIMIT 15')]
        return result


def update(changes,*,expected_revision,request_id,actor='settings',jarvis_request_id=None):
    changes=validate(changes);request_id=identity(request_id)
    encoded=json.dumps(changes,sort_keys=True,separators=(',',':'))
    with closing(connect()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        prior=conn.execute('SELECT * FROM app_settings_audit WHERE request_id=?',(request_id,)).fetchone()
        if prior:
            if prior['changes_json']!=encoded or prior['actor']!=actor:raise Conflict('Request ID belongs to a different update.')
            return {'success':True,'paper':True,**_snapshot(conn),'receipt':receipt(prior)}
        current=_snapshot(conn)
        if expected_revision!=current['revision']:raise Conflict('Settings changed elsewhere. Refresh before saving.')
        proposed={**current['settings'],**changes}
        if set(changes)&{'position_size_percent','max_order_size_percent','max_position_size_percent'}:
            if proposed['position_size_percent']>min(proposed['max_order_size_percent'],proposed['max_position_size_percent']):
                raise ValueError('Position size cannot exceed maximum order or position size; change related limits together.')
        stamp=datetime.now(timezone.utc).isoformat()
        before={key:current['settings'][key] for key in changes}
        conn.executemany('INSERT INTO app_settings VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
            [(key,'true' if value is True else 'false' if value is False else format(value,'.12g'),stamp) for key,value in changes.items()])
        saved=_snapshot(conn);change_id=str(uuid.uuid4())
        conn.execute('INSERT INTO app_settings_audit VALUES (?,?,?,?,?,?,?,?)',
            (change_id,request_id,actor,stamp,encoded,json.dumps(before),json.dumps(changes),saved['revision']))
        result={'success':True,'paper':True,**saved,'receipt':receipt(conn.execute('SELECT * FROM app_settings_audit WHERE id=?',(change_id,)).fetchone())}
        if jarvis_request_id:
            conn.execute('UPDATE jarvis_requests SET receipt_json=? WHERE id=?',(json.dumps(result),jarvis_request_id))
        conn.commit()
        return result


def undo(change_id,*,expected_revision,request_id):
    identity(change_id)
    with closing(connect()) as conn:
        row=conn.execute('SELECT * FROM app_settings_audit WHERE id=?',(change_id,)).fetchone()
        if not row:raise ValueError('Saved change not found.')
        if expected_revision!=row['revision_after']:raise Conflict('A newer setting change exists. Refresh and review first.')
        changes=json.loads(row['before_json'])
    return update(changes,expected_revision=expected_revision,request_id=request_id,actor='undo')
