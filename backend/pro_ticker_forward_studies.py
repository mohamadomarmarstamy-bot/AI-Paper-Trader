"""Pre-registered minute-bar research. No orders, training, tuning or trade P/L."""
from contextlib import closing
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import hashlib
import json
import re
import time
import uuid
import database
from pro_ticker_signal_proposals import _uuid
from pro_ticker_broker_admission import _number
from pro_ticker_research_evidence import research_report

VERSION='pro_ticker_intraday_forward_v1'
METHODS=('one_close_above_prior_high_v1','two_closes_above_prior_high_v1')
MINUTES=60
NY=ZoneInfo('America/New_York')
SCHEMA='''CREATE TABLE IF NOT EXISTS pro_ticker_forward_studies (
 signal_id TEXT PRIMARY KEY,plan_json TEXT NOT NULL,lease_until REAL NOT NULL DEFAULT 0,
 lease_owner TEXT,last_error TEXT);
 CREATE TABLE IF NOT EXISTS pro_ticker_forward_study_outcomes (
 signal_id TEXT PRIMARY KEY,recorded_at TEXT NOT NULL,evidence_json TEXT NOT NULL,report_json TEXT NOT NULL);'''


class StudyNotDue(ValueError):pass
class StudyConflict(ValueError):pass


def _stamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timezone required.')
    return result.astimezone(timezone.utc)


def _text(value):return format(value,'f')


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':')).encode()).hexdigest()


def choose_session_window(calendar, *, registered_at):
    if not isinstance(calendar,list) or not 1<=len(calendar)<=8:raise ValueError('Bounded trading calendar required.')
    now=_stamp(registered_at);first=now.astimezone(NY).date();previous=None;choices=[]
    for row in calendar:
        day=date.fromisoformat(row['date'])
        if not first<=day<=first+timedelta(days=7) or previous is not None and day<=previous:
            raise ValueError('Trading calendar dates invalid or repeated.')
        previous=day
        if not all(isinstance(row.get(k),str) and re.fullmatch(r'\d{2}:\d{2}',row[k]) for k in ('open','close')):
            raise ValueError('Explicit regular-session boundaries required.')
        opened=datetime.fromisoformat(day.isoformat()+'T'+row['open']).replace(tzinfo=NY).astimezone(timezone.utc)
        closed=datetime.fromisoformat(day.isoformat()+'T'+row['close']).replace(tzinfo=NY).astimezone(timezone.utc)
        if not timedelta(minutes=60)<=closed-opened<=timedelta(hours=12):raise ValueError('Invalid regular session duration.')
        anchor=max(opened,now.replace(second=0,microsecond=0)+timedelta(minutes=1))
        if anchor+timedelta(minutes=MINUTES)<=closed:choices.append((anchor,closed,{k:row[k] for k in ('date','open','close')}))
    if not choices:raise ValueError('No complete future regular-session study window.')
    start,closed,row=choices[0];end=start+timedelta(minutes=MINUTES)
    return {'start':start.isoformat(),'end_exclusive':end.isoformat(),
            'due_at':(end+timedelta(seconds=60)).isoformat(),'session':row,
            'session_close':closed.isoformat()}


def register_study(signal_id, *, context, clock=time.time):
    signal_id=_uuid(signal_id);context.validate()
    fingerprint=hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12]
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN')
        existing=conn.execute('SELECT plan_json FROM pro_ticker_forward_studies WHERE signal_id=?',(signal_id,)).fetchone()
        if existing:
            saved_plan=json.loads(existing[0])
            if saved_plan['account_fingerprint']!=fingerprint:raise ValueError('Study belongs to another dedicated account.')
            return {'paper':True,'research_only':True,'order_submitted':False,'duplicate':True,'plan':saved_plan}
        row=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews WHERE request_id=?',(signal_id,)).fetchone()
        if row is None or row['status']!='graded':raise ValueError('Completed saved review required.')
        grade=json.loads(row['grade_json']);saved=grade.get('research_context')
    now=clock();registered=datetime.fromtimestamp(now,timezone.utc).isoformat()
    if not isinstance(saved,dict) or research_report(saved['evidence'])!=saved['report']:
        raise ValueError('Reproducible saved research evidence required.')
    research=saved['report']
    if ('fresh_completed_daily_history' in research['missing_information'] or research['symbol']!=row['symbol'] or
            not 0<=now-_stamp(row['completed_at']).timestamp()<=300 or
            not 0<=now-_stamp(research['observed_at']).timestamp()<=300):
        raise ValueError('Fresh, already-completed research context required.')
    gateway=context.gateway();account=gateway.read_account()
    if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
    first=datetime.fromtimestamp(now,NY).date();calendar=gateway.read_research_calendar(first.isoformat(),(first+timedelta(days=7)).isoformat())
    window=choose_session_window(calendar,registered_at=registered)
    plan={'version':VERSION,'signal_id':signal_id,'symbol':row['symbol'],'registered_at':registered,
          'methods':list(METHODS),'horizon_minutes':MINUTES,**window,
          'prior_high':str(_number(research['indicators']['prior_20_session_high'])),
          'average_daily_volume':str(_number(research['indicators']['average_daily_volume'])),
          'market_regime':research['market_regime'],'grade':grade['grade'],'grade_version':grade['version'],
          'research_evidence_sha256':research['evidence_sha256'],
          'account_fingerprint':hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12],
          'research_only':True,'order_execution_authorized':False,'training_eligible':False}
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT plan_json FROM pro_ticker_forward_studies WHERE signal_id=?',(signal_id,)).fetchone()
        if existing:
            saved_plan=json.loads(existing[0])
            if saved_plan['account_fingerprint']!=fingerprint:raise ValueError('Study belongs to another dedicated account.')
            return {'paper':True,'research_only':True,'order_submitted':False,'duplicate':True,'plan':saved_plan}
        if (not 0<=clock()-now<=30 or clock()>=_stamp(plan['start']).timestamp()
                or not 0<=clock()-_stamp(row['completed_at']).timestamp()<=300
                or not 0<=clock()-_stamp(research['observed_at']).timestamp()<=300):
            raise ValueError('Registration expired before future window.')
        conn.execute('INSERT INTO pro_ticker_forward_studies(signal_id,plan_json) VALUES (?,?)',(signal_id,json.dumps(plan,allow_nan=False)));conn.commit()
    return {'paper':True,'research_only':True,'order_submitted':False,'duplicate':False,'plan':plan}


def normalize_path(raw, plan, *, observed_at):
    if not isinstance(raw,dict) or raw.get('symbol')!=plan['symbol'] or raw.get('next_page_token') not in (None,''):
        raise ValueError('Complete symbol-specific minute history required.')
    rows=raw.get('bars');start=_stamp(plan['start']);end=_stamp(plan['end_exclusive'])
    if not isinstance(rows,list) or len(rows)!=MINUTES or end-start!=timedelta(minutes=MINUTES) or _stamp(observed_at)<_stamp(plan['due_at']):
        raise ValueError('Completed full study window required.')
    bars=[]
    for i,row in enumerate(rows):
        stamp=_stamp(row['t'])
        if stamp!=start+timedelta(minutes=i):raise ValueError('Missing, duplicate, unordered or out-of-window minute.')
        values={key:_number(row[key]) for key in ('o','h','l','c','v')}
        if not Decimal('.000001')<=values['l']<=min(values['o'],values['c'])<=max(values['o'],values['c'])<=values['h']<=Decimal('1000000'):
            raise ValueError('Invalid minute prices.')
        if values['v']<0 or values['v']!=values['v'].to_integral_value():raise ValueError('Whole-share volume required.')
        bars.append({'t':stamp.isoformat(),**{k:format(v.normalize(),'f') for k,v in values.items()}})
    return bars


def study_report(plan,evidence):
    if (plan.get('version')!=VERSION or plan.get('methods')!=list(METHODS) or plan.get('horizon_minutes')!=MINUTES
            or _stamp(plan['registered_at'])>=_stamp(plan['start'])):raise ValueError('Unchanged pre-registered policy required.')
    bars=normalize_path(evidence['path'],plan,observed_at=evidence['observed_at']);high=_number(plan['prior_high'])
    if high<=0:raise ValueError('Positive prior-high evidence required.')
    outcomes=[]
    for method,confirmations in zip(METHODS,(1,2)):
        index=None
        # Confirmation uses only already-closed bars. Reference entry is the
        # FOLLOWING minute's open, never the earlier close that generated it.
        for i in range(confirmations-1,len(bars)-1):
            if all(Decimal(bars[j]['c'])>high for j in range(i-confirmations+1,i+1)):
                index=i+1;break
        result={'method':method,'status':'not_triggered','entry_at':None,'entry_reference':None,
                'quote_return_percent':None,'false_breakout':None,'max_favorable_percent':None,
                'max_adverse_percent':None,'entry_minute_volume':None,'holding_minutes':None,'entry_gap_to_prior_high_percent':None,
                'exit_reference':None,'quoted_spread_percent':None,
                'spread_sensitivity_return_percent':None,'net_pnl':None,'trade_pnl':None}
        if index is not None:
            entry=Decimal(bars[index]['o']);close=Decimal(bars[-1]['c']);following=bars[index:]
            ret=(close/entry-1)*100
            result.update(status='observed',entry_at=bars[index]['t'],entry_reference=str(entry),
                          quote_return_percent=_text(ret),false_breakout=any(Decimal(b['c'])<=high for b in following),
                          max_favorable_percent=_text((max(Decimal(b['h']) for b in following)/entry-1)*100),
                          max_adverse_percent=_text((min(Decimal(b['l']) for b in following)/entry-1)*100),
                          entry_minute_volume=int(Decimal(bars[index]['v'])),holding_minutes=len(following),
                          entry_gap_to_prior_high_percent=_text((entry/high-1)*100),exit_reference=str(close))
            quote=evidence.get('quotes',{}).get(method)
            try:
                at=_stamp(quote['t']);bid=_number(quote['bp']);ask=_number(quote['ap'])
                if not Decimal('.000001')<=bid<=ask<=Decimal('1000000') or not 0<=(at-_stamp(bars[index]['t'])).total_seconds()<=2:raise ValueError('Invalid entry quote.')
                spread=(ask-bid)/((ask+bid)/2)*100
                result.update(quoted_spread_percent=_text(spread),spread_sensitivity_return_percent=_text(ret-spread))
            except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):pass
        outcomes.append(result)
    return {'version':VERSION,'signal_id':plan['signal_id'],'symbol':plan['symbol'],'registered_at':plan['registered_at'],
            'observed_at':evidence['observed_at'],'source':'alpaca_raw_minute_bars_provider_default_feed',
            'kind':'pre_registered_forward_quote_research_not_trade_pnl','window_total_volume':sum(int(Decimal(b['v'])) for b in bars),'research_only':True,
            'order_execution_authorized':False,'model_updated':False,'training_eligible':False,
            'plan_sha256':_digest(plan),'evidence_sha256':_digest(evidence),'outcomes':outcomes,
            'limitations':['Next-minute open and final close are research references, not fills or trade profit.',
                           'False breakout means a subsequent minute close at/below the frozen prior high.',
                           'OHLC references do not prove execution or liquidity; high/low excursions do not determine stops, targets or optimal exits.',
                           'Spread sensitivity subtracts one observed provider spread; it is not actual costs or net return.',
                           'Default feed coverage, missing quotes, corporate actions, fees and manual selection limit conclusions.']}


def evaluate_study(signal_id, *, context, clock=time.time):
    signal_id=_uuid(signal_id);context.validate();owner=str(uuid.uuid4());started=clock()
    with closing(database.get_connection()) as conn:
        conn.executescript(SCHEMA);conn.execute('BEGIN IMMEDIATE')
        row=conn.execute('SELECT * FROM pro_ticker_forward_studies WHERE signal_id=?',(signal_id,)).fetchone()
        if row is None:raise ValueError('Saved forward plan required.')
        plan=json.loads(row['plan_json'])
        fingerprint=hashlib.sha256(context.expected_account_id.encode()).hexdigest()[:12]
        if plan['account_fingerprint']!=fingerprint:raise ValueError('Study belongs to a different dedicated account.')
        saved=conn.execute('SELECT evidence_json,report_json FROM pro_ticker_forward_study_outcomes WHERE signal_id=?',(signal_id,)).fetchone()
        if saved:
            report=json.loads(saved['report_json'])
            if study_report(plan,json.loads(saved['evidence_json']))!=report:raise ValueError('Saved study replay differs.')
            return {'paper':True,'order_submitted':False,'duplicate':True,'report':report}
        if clock()<_stamp(plan['due_at']).timestamp():raise StudyNotDue('The pre-registered window has not completed.')
        if row['lease_until']>clock():raise StudyConflict('This study is being evaluated.')
        conn.execute('UPDATE pro_ticker_forward_studies SET lease_until=?,lease_owner=?,last_error=NULL WHERE signal_id=?',(clock()+90,owner,signal_id));conn.commit()
    try:
        gateway=context.gateway();account=gateway.read_account()
        if not isinstance(account,dict) or account.get('id')!=context.expected_account_id:raise ValueError('Dedicated PAPER identity required.')
        raw=gateway.read_research_bars(plan['symbol'],plan['start'],plan['end_exclusive'])
        observed=datetime.fromtimestamp(clock(),timezone.utc).isoformat()
        bars=normalize_path(raw,plan,observed_at=observed)
        evidence={'observed_at':observed,'path':{'symbol':plan['symbol'],'bars':bars,'next_page_token':None},'quotes':{}}
        preliminary=study_report(plan,evidence)
        for outcome in preliminary['outcomes']:
            if outcome['status']!='observed':continue
            try:
                at=_stamp(outcome['entry_at'])
                quote=gateway.read_research_quote(plan['symbol'],at.isoformat(),(at+timedelta(seconds=2)).isoformat())
                if isinstance(quote,dict):
                    evidence['quotes'][outcome['method']]={'t':quote.get('t'),'bp':str(_number(quote.get('bp'))),'ap':str(_number(quote.get('ap')))}
            except Exception:pass
        report=study_report(plan,evidence)
        with closing(database.get_connection()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=conn.execute('SELECT * FROM pro_ticker_forward_studies WHERE signal_id=?',(signal_id,)).fetchone()
            if current['lease_owner']!=owner or not started<=clock()<current['lease_until'] or clock()-started>60 or json.loads(current['plan_json'])!=plan:
                raise StudyConflict('Study claim expired or plan changed.')
            conn.execute('INSERT INTO pro_ticker_forward_study_outcomes VALUES (?,?,?,?)',(signal_id,observed,json.dumps(evidence,allow_nan=False),json.dumps(report,allow_nan=False)))
            conn.execute('UPDATE pro_ticker_forward_studies SET lease_until=0,lease_owner=NULL,last_error=NULL WHERE signal_id=?',(signal_id,));conn.commit()
        return {'paper':True,'order_submitted':False,'duplicate':False,'report':report}
    except Exception:
        with closing(database.get_connection()) as conn:
            conn.execute("UPDATE pro_ticker_forward_studies SET lease_until=0,lease_owner=NULL,last_error='provider_or_evidence_unavailable' WHERE signal_id=? AND lease_owner=?",(signal_id,owner));conn.commit()
        raise


def study_history():
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN');exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_forward_studies'").fetchone()
        count=conn.execute('SELECT COUNT(*) FROM pro_ticker_forward_studies').fetchone()[0] if exists else 0
        rows=conn.execute('SELECT s.*,o.evidence_json,o.report_json FROM pro_ticker_forward_studies s LEFT JOIN pro_ticker_forward_study_outcomes o ON o.signal_id=s.signal_id ORDER BY s.rowid DESC LIMIT 200').fetchall() if exists else []
    items=[];groups={};cohorts={};paired=[];seen_windows=set();duplicates=0
    for row in sorted(rows,key=lambda r:(_stamp(json.loads(r['plan_json'])['registered_at']),r['signal_id'])):
        plan=json.loads(row['plan_json']);report=json.loads(row['report_json']) if row['report_json'] else None
        if report and study_report(plan,json.loads(row['evidence_json']))!=report:raise ValueError('Saved study replay differs.')
        items.append({'plan':plan,'report':report,'last_error':row['last_error'],'evaluation_in_progress':row['lease_until']>time.time()})
        if report:
            signature=(plan['symbol'],_stamp(plan['start']),_stamp(plan['end_exclusive']),Decimal(plan['prior_high']),tuple(plan['methods']))
            if signature in seen_windows:duplicates+=1;continue
            seen_windows.add(signature)
            if all(o['status']=='observed' for o in report['outcomes']):
                paired.append(Decimal(report['outcomes'][0]['quote_return_percent'])-Decimal(report['outcomes'][1]['quote_return_percent']))
            for outcome in report['outcomes']:
                group=groups.setdefault(outcome['method'],{'method':outcome['method'],'evaluated_windows':0,'triggered':0,'not_triggered':0,'positive':0,'negative':0,'flat':0,'false_breakouts':0,'returns':[],'spreads':[]})
                group['evaluated_windows']+=1
                if outcome['status']=='not_triggered':group['not_triggered']+=1;continue
                value=Decimal(outcome['quote_return_percent']);group['triggered']+=1;group['positive']+=value>0;group['negative']+=value<0;group['flat']+=value==0;group['false_breakouts']+=outcome['false_breakout'];group['returns'].append(value)
                if outcome['quoted_spread_percent'] is not None:group['spreads'].append(Decimal(outcome['quoted_spread_percent']))
                spread=Decimal(outcome['quoted_spread_percent']) if outcome['quoted_spread_percent'] is not None else None
                dimensions={'saved_grade_and_version':plan['grade_version']+':'+plan['grade'],
                            'market_regime':plan.get('market_regime') or 'unavailable',
                            'daily_liquidity':'below_50000_average_shares' if Decimal(plan['average_daily_volume'])<50000 else 'at_least_50000_average_shares',
                            'entry_spread':'unavailable' if spread is None else 'at_most_0.1_percent' if spread<=Decimal('.1') else '0.1_to_0.5_percent' if spread<=Decimal('.5') else 'above_0.5_percent'}
                for dimension,label in dimensions.items():
                    cohort=cohorts.setdefault((outcome['method'],dimension,label),{'returns':[],'false_breakouts':0})
                    cohort['returns'].append(value);cohort['false_breakouts']+=outcome['false_breakout']

    summaries=[]
    for group in groups.values():
        returns=group.pop('returns');spreads=group.pop('spreads')
        group.update(mean_quote_return_percent=_text(sum(returns)/len(returns)) if returns else None,
                     spread_samples=len(spreads),mean_quoted_spread_percent=_text(sum(spreads)/len(spreads)) if spreads else None)
        summaries.append(group)
    difference=sum(paired)/len(paired) if paired else None
    comparison={'paired_triggered_count':len(paired),'mean_one_minus_two_return_percent':_text(difference) if difference is not None else None,
                'review_candidate_method':METHODS[0] if len(paired)>=20 and difference>0 else METHODS[1] if len(paired)>=20 and difference<0 else None,
                'requires_explicit_approval':True,'applied':False,
                'limitation':'At least 20 paired triggered windows are required for a review candidate, not statistical significance. Different entry times imply different holding periods; overlapping windows and correlated stocks are not independent. Costs and selection bias remain unverified.'}
    return {'version':VERSION,'paper':True,'research_only':True,'order_execution_authorized':False,'training_enabled':False,'model_updated':False,
            'paired_comparison':comparison,'cohorts':[{'method':method,'dimension':dimension,'label':label,'samples':len(values['returns']),'false_breakouts':values['false_breakouts'],'mean_quote_return_percent':_text(sum(values['returns'])/len(values['returns']))} for (method,dimension,label),values in sorted(cohorts.items())],
            'total_count':count,'window_count':len(items),'has_more':count>len(items),'studies':list(reversed(items)),'groups':summaries,'duplicate_window_count':duplicates,
            'limitation':'Latest 200 manually selected plans only. Registered future windows are distinct from retrospective cohorts; quote research is not trade P/L or predictive validation.'}
