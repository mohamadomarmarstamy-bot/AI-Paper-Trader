"""Durable hourly Jarvis PAPER digests using the existing owner's mail settings."""
import asyncio
from datetime import datetime
from html import escape
import json
import logging
import os
import time
from zoneinfo import ZoneInfo

import solana_http
import jarvis_health_mail
import jarvis_audit

LOG = logging.getLogger(__name__)
ET = ZoneInfo('America/New_York')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS local_strategy_events(
 id INTEGER PRIMARY KEY, at REAL NOT NULL, strategy TEXT NOT NULL,
 kind TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS local_strategy_event_time ON local_strategy_events(at);
CREATE TABLE IF NOT EXISTS jarvis_digest_schedule(
 id INTEGER PRIMARY KEY CHECK(id=1), next_at REAL NOT NULL, last_end REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jarvis_digest_mail(
 id TEXT PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL, payload TEXT NOT NULL,
 due REAL NOT NULL, lease REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 first_attempt REAL, sent REAL, provider_id TEXT, status TEXT NOT NULL DEFAULT 'pending'
);
''' + jarvis_health_mail.SCHEMA


def enabled():
    return os.getenv('JARVIS_HOURLY_EMAIL_ENABLED','true').strip().lower() not in ('0','false','no','off')


def configured():
    return all(os.getenv(key,'').strip() for key in ('RESEND_API_KEY','HEALTH_ALERT_EMAIL_TO','HEALTH_ALERT_EMAIL_FROM'))


def event(conn, at, strategy, kind, detail):
    conn.execute('INSERT INTO local_strategy_events(at,strategy,kind,detail) VALUES (?,?,?,?)',
                 (at,strategy,kind,json.dumps(detail)))


def collect(conn, state, start, end):
    candidates = conn.execute('SELECT * FROM local_strategies ORDER BY created DESC').fetchall()
    # The first digest can include proposals admitted before event logging existed.
    events = [dict(row) for row in conn.execute(
        'SELECT * FROM local_strategy_events WHERE at>? AND at<=? ORDER BY at,id',(start,end))]
    known = {row['strategy'] for row in events if row['kind']=='added'}
    for candidate in candidates:
        if start < candidate['created'] <= end and candidate['id'] not in known:
            events.append({'at':candidate['created'],'strategy':candidate['id'],'kind':'added',
                           'detail':json.dumps({'name':json.loads(candidate['rule'])['name']})})
    # One grouping covers all versions. Main Jarvis is a separate portfolio,
    # not added to the strategy-experiment totals.
    stats = {}
    for row in conn.execute("""
        SELECT bot,json_extract(data,'$.policy.local_rule.id') AS version,
          count(*) AS closed,count(DISTINCT mint) AS coins,
          COALESCE(sum(pnl),0) AS net,
          count(CASE WHEN closed>? AND closed<=? THEN 1 END) AS hour_closed,
          COALESCE(sum(CASE WHEN closed>? AND closed<=? THEN pnl ELSE 0 END),0) AS hour_net
        FROM trades WHERE closed<=? AND (bot LIKE 'research-%' OR bot='jarvis')
        GROUP BY bot,version
    """,(start,end,start,end,end)):
        stats[(row['bot'],row['version'])] = dict(row)
    touched = {row['strategy'] for row in events}
    rows = []
    all_hour, all_net, all_closed, all_open = 0,0,0,0
    for candidate in candidates:
        rule = json.loads(candidate['rule'])
        sample = stats.get((candidate['slot'],candidate['id']),{})
        portfolio = state['bots'].get(candidate['slot'],{})
        positions = [p for p in portfolio.get('positions',{}).values()
                     if p.get('policy',{}).get('local_rule',{}).get('id')==candidate['id']]
        item = {'id':candidate['id'],'name':rule['name'],'hypothesis':rule['hypothesis'],
                'entry':rule['entry'],'exit':rule['exit'],'status':candidate['status'],
                'created':candidate['created'], 'sources':json.loads(candidate['sources']),
                'net':sample.get('net',0)/1e6,'hour_net':sample.get('hour_net',0)/1e6,
                'closed':sample.get('closed',0),'coins':sample.get('coins',0),
                'hour_closed':sample.get('hour_closed',0),'open':len(positions)}
        all_hour += item['hour_net'];all_net += item['net'];all_closed += item['hour_closed'];all_open += item['open']
        if (candidate['status'] in ('testing','proven') or item['hour_closed'] or item['open'] or candidate['id'] in touched):
            rows.append(item)
    last_seen = conn.execute("SELECT value FROM local_worker_state WHERE name='last_seen'").fetchone()
    last_seen = float(last_seen['value']) if last_seen else None
    main_stats = [sample for (bot,_),sample in stats.items() if bot=='jarvis']
    return {'start':start,'end':end,'events':events,'strategies':rows,
            'hour_net':all_hour,'net':all_net,'closed':all_closed,'open':all_open,
            'main_hour_net':sum(s['hour_net'] for s in main_stats)/1e6,
            'main_net':sum(s['net'] for s in main_stats)/1e6,
            'last_seen':last_seen,'worker_online':last_seen is not None and 0<=end-last_seen<=600,
            'paper_enabled':state['enabled'],
            'quote_error':state.get('error')}


def money(value):
    return ('+' if value>0 else '−' if value<0 else '')+'$'+f'{abs(value):,.2f}'


def profit_color(value):
    return '#047857' if value > 0 else '#b91c1c' if value < 0 else '#64748b'


def next_delivery(now):
    candidate=(int(now)//3600+1)*3600
    while 0 < datetime.fromtimestamp(candidate,ET).hour < 9:
        candidate+=3600
    return candidate


def overnight_rows(conn, start, end):
    rows=[]
    at=start
    while at < end:
        until=min(at+3600,end)
        counts=conn.execute("SELECT count(*) AS n,COALESCE(sum(CASE WHEN bot LIKE 'research-%' THEN pnl ELSE 0 END),0) AS research,COALESCE(sum(CASE WHEN bot='jarvis' THEN pnl ELSE 0 END),0) AS main FROM trades WHERE closed>? AND closed<=? AND (bot LIKE 'research-%' OR bot='jarvis')",(at,until)).fetchone()
        changes=conn.execute("SELECT kind,count(*) AS n FROM local_strategy_events WHERE at>? AND at<=? GROUP BY kind",(at,until)).fetchall()
        rows.append({'label':datetime.fromtimestamp(at,ET).strftime('%I %p %Z')+'–'+datetime.fromtimestamp(until,ET).strftime('%I %p %Z'),'closed':counts['n'],'research':counts['research']/1e6,'main':counts['main']/1e6,'changes':', '.join(str(r['n'])+' '+r['kind'] for r in changes) or '—'})
        at=until
    return rows


def render_overnight(report, rows):
    table=''.join('<tr><td style="padding:10px">'+escape(r['label'])+'</td><td>'+str(r['closed'])+'</td><td style="color:'+profit_color(r['research'])+'">'+money(r['research'])+'</td><td style="color:'+profit_color(r['main'])+'">'+money(r['main'])+'</td><td>'+escape(r['changes'])+'</td></tr>' for r in rows)
    text='Jarvis overnight summary\n'+stamp(report['start'])+' – '+stamp(report['end'])+'\nResearch realized: '+money(report['hour_net'])+' | Lifetime: '+money(report['net'])+'\n'
    text+='\n'.join(r['label']+': '+str(r['closed'])+' closes | research '+money(r['research'])+' | Jarvis '+money(r['main'])+' | '+r['changes'] for r in rows)
    body='<html><body style="font-family:Arial;background:#f1f5f9;color:#0f172a"><div style="max-width:700px;margin:auto;padding:24px;background:white"><h1>Jarvis overnight summary</h1><p>'+escape(stamp(report['start'])+' – '+stamp(report['end']))+'</p><p>Realized this period: <strong style="color:'+profit_color(report['hour_net'])+'">'+money(report['hour_net'])+'</strong> · Lifetime: <strong style="color:'+profit_color(report['net'])+'">'+money(report['net'])+'</strong></p><table width="100%" style="font-size:12px"><tr><th>Hour</th><th>Closes</th><th>Research P/L</th><th>Jarvis P/L</th><th>Changes</th></tr>'+table+'</table><p style="font-size:12px;color:#64748b">Paper simulation only. Realized results include recorded costs and write-offs; open-position P/L is excluded. Hour labels show each activity interval.</p></div></body></html>'
    return {'subject':'Jarvis overnight summary · '+money(report['hour_net'])+' paper realized','html':body,'text':text}


def stamp(value):
    return datetime.fromtimestamp(value,ET).strftime('%b %d, %I:%M %p %Z')


def render(report):
    h = lambda text: escape(str(text),quote=True)
    period = stamp(report['start'])+' – '+stamp(report['end'])
    changes = {'added':[],'failed':[],'inactive':[],'proven':[],'promoted':[],'duplicate':[]}
    names = {s['id']:s['name'] for s in report['strategies']}
    for item in report['events']:
        detail = json.loads(item['detail'])
        changes.setdefault(item['kind'],[]).append(detail.get('name') or names.get(item['strategy'],item['strategy'][:8]))
    activity = []
    labels = {'added':'Added for paper testing','failed':'Retired after losses','inactive':'Retired for inactivity',
              'proven':'Passed the experiment checks','promoted':'Adopted by Jarvis','duplicate':'Duplicate proposals skipped'}
    for kind,label in labels.items():
        if changes[kind]:
            activity.append(f'<p style="margin:8px 0"><strong>{label}:</strong> {h(", ".join(changes[kind]))}</p>')
    if not activity:
        activity=['<p style="margin:0">No strategies were added, retired or promoted during this period.</p>']
    table_rows = []
    plain_rows = []
    for s in report['strategies']:
        color = profit_color(s['hour_net'])
        table_rows.append(f'''<tr><td style="padding:14px 10px;border-bottom:1px solid #e2e8f0">
<strong>{h(s['name'])}</strong><br><span style="font-size:12px;color:#64748b">{h(s['status'].title())} · {s['coins']}/100 coins · {s['open']} open</span></td>
<td align="right" style="padding:14px 10px;border-bottom:1px solid #e2e8f0;color:{color};white-space:nowrap">{money(s['hour_net'])}</td>
<td align="right" style="padding:14px 10px;border-bottom:1px solid #e2e8f0;color:{profit_color(s['net'])};white-space:nowrap">{money(s['net'])}</td></tr>''')
        plain_rows.append(f"{s['name']} [{s['status']}]: period {money(s['hour_net'])}; lifetime {money(s['net'])}; {s['coins']}/100 coins; {s['open']} open")
    if not table_rows:
        table_rows=['<tr><td colspan="3" style="padding:16px;color:#64748b">No research strategies are active yet.</td></tr>']
    research=[]
    for s in report['strategies']:
        if s['id'] not in {e['strategy'] for e in report['events'] if e['kind']=='added'}:
            continue
        providers = ', '.join(dict.fromkeys(source.get('title','Reference source') for source in s['sources']))
        research.append(f'''<div style="padding:16px;margin:12px 0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
<strong>{h(s['name'])}</strong><p style="line-height:1.6">{h(s['hypothesis'])}</p>
<p style="font-size:12px;line-height:1.6"><strong>Entry</strong> {h(s['entry'])}<br><strong>Exit</strong> {h(s['exit'])}</p>
<p style="font-size:11px;color:#64748b">Research: {h(providers)}. Thresholds remain untested hypotheses.</p></div>''')
    if not research:
        research=['<p style="color:#64748b">No new accepted research proposals during this period. Existing paper tests continue independently.</p>']
    worker = 'Laptop checked in recently' if report['worker_online'] else 'Laptop has not checked in recently'
    if report['last_seen']:
        worker += ' · last seen '+stamp(report['last_seen'])
    else:
        worker += ' · no connection recorded'
    notice = 'Paper entries enabled' if report['paper_enabled'] else 'Paper entries paused; existing exits remain monitored'
    if report['quote_error']:
        notice += ' · latest engine note: '+report['quote_error']
    html = f'''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f1f5f9;color:#0f172a;font-family:Arial,Helvetica,sans-serif">
<div style="display:none;max-height:0;overflow:hidden">Jarvis paper research · {money(report['hour_net'])} realized · {len(changes['added'])} new strategies</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#ffffff;border-radius:14px;overflow:hidden">
<tr><td style="padding:32px;background:#0f172a;color:#ffffff"><div style="font-size:12px;letter-spacing:3px;color:#6ee7b7">JARVIS / RESEARCH DESK</div>
<h1 style="font-size:27px;margin:14px 0 8px">Your hourly paper trading brief</h1><div style="font-size:13px;color:#cbd5e1">{h(period)}</div></td></tr>
<tr><td style="padding:28px 28px 8px"><table role="presentation" width="100%"><tr>
<td width="50%" style="padding:16px;background:#ecfdf5;border-radius:8px"><div style="font-size:12px;color:#475569">REALIZED THIS PERIOD</div><div style="font-size:29px;font-weight:bold;margin-top:8px;color:{profit_color(report['hour_net'])}">{money(report['hour_net'])}</div></td>
<td width="50%" style="padding:16px;background:#f8fafc;border-radius:8px"><div style="font-size:12px;color:#475569">EXPERIMENT LIFETIME</div><div style="font-size:29px;font-weight:bold;margin-top:8px;color:{profit_color(report['net'])}">{money(report['net'])}</div></td></tr></table>
<p style="font-size:12px;color:#64748b">{report['closed']} positions closed this period · {report['open']} experiment holdings open · all amounts simulated USD</p>
<h2 style="font-size:18px;margin-top:26px">What changed</h2>{''.join(activity)}
<h2 style="font-size:18px;margin-top:26px">Strategy scorecard</h2>
<table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px"><tr style="background:#f8fafc"><th align="left" style="padding:10px">Strategy / progress</th><th align="right" style="padding:10px">Period P/L</th><th align="right" style="padding:10px">Lifetime P/L</th></tr>{''.join(table_rows)}</table>
<p style="font-size:12px;color:#64748b">Main Jarvis portfolio, shown separately: {money(report['main_hour_net'])} this period · {money(report['main_net'])} lifetime. It is not included in the experiment totals above.</p>
<h2 style="font-size:18px;margin-top:26px">Research behind new proposals</h2>{''.join(research)}
<h2 style="font-size:18px;margin-top:26px">System status</h2><p style="font-size:13px;line-height:1.6">{h(worker)}<br>{h(notice)}</p>
</td></tr><tr><td style="padding:22px 28px;background:#f8fafc;font-size:11px;line-height:1.7;color:#64748b">
PAPER SIMULATION ONLY · Realized P/L includes recorded simulated costs and write-offs. Open-position gains and losses are excluded. Retirement preserves history and continues existing exit monitoring. A 100-coin test is evidence gathering, not a profitability guarantee. Research attempts that never submit a proposal are not fully visible to the server.</td></tr></table></td></tr></table></body></html>'''
    text = f"JARVIS — HOURLY PAPER TRADING BRIEF\n{period}\n\nExperiment realized this period: {money(report['hour_net'])}\nExperiment lifetime realized: {money(report['net'])}\nClosed this period: {report['closed']} | Open: {report['open']}\n\n"
    for kind,label in labels.items():
        if changes[kind]:text += label+': '+', '.join(changes[kind])+'\n'
    text += '\nSTRATEGY SCORECARD\n'+'\n'.join(plain_rows)
    for s in report['strategies']:
        if s['id'] in {e['strategy'] for e in report['events'] if e['kind']=='added'}:
            text += f"\n\nRESEARCH — {s['name']}\n{s['hypothesis']}\nEntry: {s['entry']}\nExit: {s['exit']}"
    text += f"\n\nMain Jarvis separately: period {money(report['main_hour_net'])}; lifetime {money(report['main_net'])}.\n{worker}\n{notice}\n\nPaper simulation only. Unrealized P/L excluded. Retired strategies retain history. Unsubmitted research attempts are not fully visible to the server."
    return {'subject':f"Jarvis hourly brief · {money(report['hour_net'])} paper realized · {len(changes['added'])} added",
            'html':html,'text':text}


def tick(api, clock=time.time, post=solana_http.post):
    if not enabled() or not configured():
        return 'disabled_or_unconfigured'
    now=clock()
    with api.connection() as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        schedule=conn.execute('SELECT * FROM jarvis_digest_schedule WHERE id=1').fetchone()
        state,_=api.read(conn)
        jarvis_health_mail.queue(conn,state,now)
        local=datetime.fromtimestamp(now,ET)
        boundary=int(now)//3600*3600
        if schedule is None:
            midnight=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            conn.execute('INSERT INTO jarvis_digest_schedule VALUES (1,?,?)',(next_delivery(now),midnight if local.hour<9 else boundary))
            schedule=conn.execute('SELECT * FROM jarvis_digest_schedule WHERE id=1').fetchone()
        elif schedule['next_at'] % 3600:
            conn.execute('UPDATE jarvis_digest_schedule SET next_at=? WHERE id=1',(next_delivery(now),))
            schedule=conn.execute('SELECT * FROM jarvis_digest_schedule WHERE id=1').fetchone()
        # Quiet hours also defer delivery retries. Health alerts use their own transport.
        if 0 < local.hour < 9:
            return 'quiet_hours'
        if now>=schedule['next_at']:
            start=schedule['last_end']
            if local.hour==9:
                midnight=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
                start=min(start,midnight)
                start=int(start)//3600*3600
            report=collect(conn,state,start,boundary)
            overnight=local.hour==9 and boundary-start>3600
            content=render_overnight(report,overnight_rows(conn,start,boundary)) if overnight else render(report)
            payload={'from':os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(),
                     'to':[os.environ['HEALTH_ALERT_EMAIL_TO'].strip()],**content}
            note=jarvis_audit.brief(jarvis_audit.latest(conn))
            payload['text']+='\n\n'+note
            payload['html']=payload['html'].replace('</body>','<p style="font-family:Arial;font-size:12px;padding:16px;color:#475569">'+escape(note)+'</p></body>')
            ident='jarvis-hourly/'+str(boundary)
            # Fold unsent old summaries into the catch-up report instead of flooding at 9 AM.
            if overnight:
                conn.execute("UPDATE jarvis_digest_mail SET status='attention' WHERE status='pending' AND first_attempt IS NOT NULL AND end<=?",(boundary,))
            conn.execute("UPDATE jarvis_digest_mail SET status='superseded' WHERE status='pending' AND first_attempt IS NULL AND end<=?",(boundary,))
            conn.execute('INSERT OR IGNORE INTO jarvis_digest_mail(id,start,end,payload,due) VALUES (?,?,?,?,?)',
                         (ident,start,boundary,json.dumps(payload),now))
            conn.execute('INSERT OR REPLACE INTO jarvis_digest_schedule VALUES (1,?,?)',(next_delivery(now),boundary))
        row=conn.execute("SELECT * FROM jarvis_digest_mail WHERE status='pending' AND due<=? AND lease<=? ORDER BY end LIMIT 1",(now,now)).fetchone()
        if not row:return 'not_due'
        if row['first_attempt'] is not None and now-row['first_attempt']>=23*3600:
            conn.execute("UPDATE jarvis_digest_mail SET status='attention' WHERE id=?",(row['id'],))
            return 'delivery_uncertain'
        conn.execute('UPDATE jarvis_digest_mail SET lease=?,attempts=attempts+1,first_attempt=COALESCE(first_attempt,?) WHERE id=?',
                     (now+60,now,row['id']))
    accepted=False;provider_id=None
    try:
        response=post('https://api.resend.com/emails',headers={
            'Authorization':'Bearer '+os.environ['RESEND_API_KEY'],
            'Idempotency-Key':row['id'],'Content-Type':'application/json'},
            json=json.loads(row['payload']),timeout=8,allow_redirects=False)
        if response.status_code in (200,201,202):
            provider_id=response.json().get('id')
            accepted=isinstance(provider_id,str) and bool(provider_id)
    except Exception:
        pass
    with api.connection() as conn, conn:
        conn.execute('UPDATE jarvis_digest_mail SET status=?,sent=?,provider_id=?,lease=0,due=? WHERE id=?',
                     ('accepted' if accepted else 'pending',clock() if accepted else None,provider_id,
                      now+min(3600,60*2**min(row['attempts'],5)),row['id']))
    LOG.info('Jarvis hourly brief %s', 'accepted by email provider' if accepted else 'delivery retry scheduled')
    return 'accepted' if accepted else 'retry'


def status(conn):
    schedule=conn.execute('SELECT next_at FROM jarvis_digest_schedule WHERE id=1').fetchone()
    last=conn.execute("SELECT end,sent FROM jarvis_digest_mail WHERE status='accepted' AND id LIKE 'jarvis-hourly/%' ORDER BY end DESC LIMIT 1").fetchone()
    return {'enabled':enabled(),'configured':configured(),'next_at':schedule['next_at'] if schedule else None,
            'last_accepted':dict(last) if last else None,
            'pending':conn.execute("SELECT count(*) FROM jarvis_digest_mail WHERE status='pending'").fetchone()[0],
            'attention':conn.execute("SELECT count(*) FROM jarvis_digest_mail WHERE status='attention'").fetchone()[0]}


async def worker():
    import jarvis_test_bot as api
    while True:
        try:
            await asyncio.to_thread(tick,api)
        except Exception:
            LOG.exception('Jarvis hourly brief cycle failed')
        await asyncio.sleep(20)
