"""Owner settings for future manual Pro Ticker PAPER entries."""
from contextlib import closing
import json
import math
import database

SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_entry_settings(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,settings_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pro_ticker_entry_settings_audit(revision INTEGER PRIMARY KEY,previous_json TEXT NOT NULL,settings_json TEXT NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);'''
DEFAULTS={'dollars_per_stock':2000.0,'stop_loss_percent':2.0,'take_profit_percent':4.0}
class Conflict(ValueError):pass

def validate(values):
    if not isinstance(values,dict) or set(values)!=set(DEFAULTS):raise ValueError('Provide dollars per stock, stop loss and take profit only.')
    for key,value in values.items():
        maximum=50000 if key=='dollars_per_stock' else 25 if key=='stop_loss_percent' else 100
        minimum=1 if key=='dollars_per_stock' else .1
        if type(value) not in (float,int) or not minimum<=value<=maximum or not math.isfinite(value):raise ValueError('Unsupported amount or percentage.')
        if key=='dollars_per_stock' and round(value,2)!=value:raise ValueError('Dollar amount supports cents only.')
    return dict(values)

def read(defaults=None,connection=None):
    if connection is None:
        with closing(database.get_connection()) as conn:return read(defaults,conn)
    exists=connection.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_entry_settings'").fetchone()
    row=connection.execute('SELECT * FROM pro_ticker_entry_settings WHERE id=1').fetchone() if exists else None
    return {'paper':True,'revision':row['revision'] if row else 0,'settings':validate(json.loads(row['settings_json']) if row else defaults or DEFAULTS),'effect':'Future Pro Ticker submissions only. Whole shares and existing risk limits apply. Existing positions and orders keep their saved protection.'}

def save(payload,defaults=None):
    if not isinstance(payload,dict) or set(payload)!={'expected_revision','settings'}:raise ValueError('Revision and settings required.')
    if type(payload['expected_revision']) is not int or payload['expected_revision']<0:raise ValueError('Invalid revision.')
    values=validate(payload['settings'])
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE');current=read(defaults,conn)
        if current['revision']!=payload['expected_revision']:raise Conflict('Settings changed. Reload before saving.')
        revision=current['revision']+1
        conn.execute('INSERT INTO pro_ticker_entry_settings_audit(revision,previous_json,settings_json) VALUES (?,?,?)',(revision,json.dumps(current['settings']),json.dumps(values)))
        conn.execute('INSERT INTO pro_ticker_entry_settings VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,settings_json=excluded.settings_json',(revision,json.dumps(values)));conn.commit()
        return read(defaults,conn)
