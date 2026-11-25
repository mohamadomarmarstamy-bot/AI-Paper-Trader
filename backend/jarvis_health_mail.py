"""One alert per quiet/stale Jarvis episode, using the durable email outbox."""
from html import escape
import json
import os

import jarvis_focus

INTERVAL=1800
SCHEMA='''CREATE TABLE IF NOT EXISTS jarvis_health_monitor(
 id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL);'''


def queue(conn,state,now):
    if not jarvis_focus.enabled():
        return
    from jarvis_hourly_mail import stamp
    saved=conn.execute('SELECT state FROM jarvis_health_monitor WHERE id=1').fetchone()
    previous=json.loads(saved[0]) if saved else {}
    running=bool(state['enabled'])
    times=[b.get('last_trade_at',0) for name,b in state['bots'].items()
           if name=='jarvis' or name.startswith('research-')]
    times += [p.get('opened',0) for name,b in state['bots'].items()
              if name=='jarvis' or name.startswith('research-') for p in b['positions'].values()]
    row=conn.execute("SELECT max(closed) FROM trades WHERE (bot='jarvis' OR bot LIKE 'research-%') AND json_extract(data,'$.paper_writeoff') IS NULL").fetchone()
    last=max([row[0] or 0,*times])
    resumed=running and not previous.get('enabled',False)
    advanced=last>previous.get('last_trade',0)
    monitor={**previous,'enabled':running,'last_trade':max(last,previous.get('last_trade',0))}
    monitor.setdefault('anchor',now)
    monitor.setdefault('health_anchor',now)
    if not running or resumed or advanced:
        monitor.update(anchor=now if resumed or not running else last,idle_alerted=False)
        conn.execute("UPDATE jarvis_digest_mail SET status='cancelled' WHERE id LIKE 'jarvis-health/idle/%' AND status='pending'")
    if not running or resumed:
        monitor.update(health_anchor=now,stale_alerted=False)
        conn.execute("UPDATE jarvis_digest_mail SET status='cancelled' WHERE id LIKE 'jarvis-health/stale/%' AND status='pending'")
    cycle=state.get('last_cycle') or 0
    stale=running and now-max(cycle,monitor['health_anchor'])>=INTERVAL
    if not stale:
        monitor['stale_alerted']=False
        conn.execute("UPDATE jarvis_digest_mail SET status='cancelled' WHERE id LIKE 'jarvis-health/stale/%' AND status='pending'")
    alerts=[]
    if running and now-monitor['anchor']>=INTERVAL and not monitor.get('idle_alerted'):
        monitor['idle_alerted']=True
        alerts.append(('idle','No research trades for 30 minutes',
                       'Jarvis Trader and its local AI research strategies have made no successful paper buys or sells for at least 30 minutes.'))
    if stale and not monitor.get('stale_alerted'):
        monitor['stale_alerted']=True
        alerts.append(('stale','Trading worker heartbeat delayed',
                       'The Jarvis paper engine has not completed a trading cycle for at least 30 minutes. Existing exits may be delayed; check Health.'))
    for kind,title,reason in alerts:
        note=('No eligible signals can also cause a quiet period; this alone does not prove a fault.' if kind=='idle'
              else 'This alert is independent of the laptop researcher; check the server trading worker.')
        lines=[reason,note,'Checked: '+stamp(now),
               'Last successful research trade: '+(stamp(last) if last else 'None recorded'),
               'Last engine cycle: '+(stamp(cycle) if cycle else 'None recorded'),
               'Latest engine note: '+str(state.get('error') or 'None reported'),
               'One email per episode while enabled. Pausing the paper engine suppresses these alerts. PAPER only.']
        payload={'from':os.environ['HEALTH_ALERT_EMAIL_FROM'].strip(),
                 'to':[os.environ['HEALTH_ALERT_EMAIL_TO'].strip()],
                 'subject':'Jarvis health · '+title,'text':'\n\n'.join(lines),
                 'html':'<div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#0f172a"><h1 style="background:#0f172a;color:white;padding:24px">Jarvis health</h1><h2>'+escape(title)+'</h2>'+''.join('<p style="line-height:1.6">'+escape(line)+'</p>' for line in lines)+'</div>'}
        conn.execute('INSERT OR IGNORE INTO jarvis_digest_mail(id,start,end,payload,due) VALUES (?,?,?,?,?)',
                     (f'jarvis-health/{kind}/{int(now)}',monitor['anchor'],now,json.dumps(payload),now))
    conn.execute('INSERT OR REPLACE INTO jarvis_health_monitor VALUES (1,?)',(json.dumps(monitor),))
