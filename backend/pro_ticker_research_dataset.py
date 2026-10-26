"""Auditable, loss-inclusive research export; never a model training input feed."""
from contextlib import closing
import csv
import io
import json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response
import database
from pro_ticker_signal_learning import outcomes_for_signals, KIND

VERSION='pro_ticker_research_evidence_v1'
FEATURE_KEYS=('sma_20','sma_50','rsi','five_day_momentum','volume_ratio')
EXCLUSIONS=('verified_execution_outcome_missing','indicator_asof_times_unverified',
            'reference_precedes_analysis_completion','costs_and_corporate_actions_unmodeled',
            'manual_sample_selection_bias','out_of_sample_evaluation_missing')


def research_dataset(*, limit=500):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=500:
        raise ValueError('Dataset limit must be 1 to 500.')
    with closing(database.get_connection()) as conn:
        conn.execute('BEGIN')
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE name='pro_ticker_manual_signal_reviews'").fetchone()
        total=conn.execute('SELECT COUNT(*) FROM pro_ticker_manual_signal_reviews').fetchone()[0] if exists else 0
        reviews=conn.execute('SELECT * FROM pro_ticker_manual_signal_reviews ORDER BY submitted_at DESC,request_id DESC LIMIT ?',(limit,)).fetchall() if exists else []
        outcomes=outcomes_for_signals(conn,[r['request_id'] for r in reviews])
    rows=[]
    for review in reviews:
        grade=json.loads(review['grade_json']) if review['grade_json'] else None
        snapshot=json.loads(review['snapshot_json']) if review['snapshot_json'] else None
        graded=isinstance(grade,dict) and grade.get('status')=='graded'
        indicators=grade.get('indicators',{}) if graded else {}
        # This whitelist deliberately excludes later prices, allocations, exits,
        # journal diagnostics and all outcomes from the entry feature object.
        features={'version':grade['version'],'grade':grade['grade'],'score':grade['score'],
                  'signal':grade['signal'],'heuristic_confidence':grade['confidence'],
                  **{k:indicators.get(k) for k in FEATURE_KEYS}} if graded else None
        outcome=outcomes.get(review['request_id'])
        exclusions=list(EXCLUSIONS)
        if not graded:exclusions.append('completed_grade_missing')
        if not snapshot or snapshot.get('fresh') is not True:exclusions.append('fresh_entry_reference_missing')
        if outcome is None:exclusions.append('forward_quote_outcome_missing')
        rows.append({'request_id':review['request_id'],'symbol':review['symbol'],
                     'submitted_at':review['submitted_at'],'completed_at':review['completed_at'],
                     'review_status':review['status'],'entry_features':features,
                     'entry_reference':{k:snapshot.get(k) for k in ('price','market_timestamp','fresh','captured_at')} if snapshot else None,
                     'future_outcome':outcome,'training_eligible':False,'training_exclusions':exclusions})
    evaluated=sum(r['future_outcome'] is not None for r in rows)
    return {'paper':True,'version':VERSION,'kind':KIND,'training_enabled':False,'model_updated':False,
            'training_ready_samples':0,'total_count':total,'window_count':len(rows),'has_more':total>len(rows),
            'graded_count':sum(r['entry_features'] is not None for r in rows),'evaluated_count':evaluated,
            'unevaluated_count':len(rows)-evaluated,'rows':rows,
            'reason':'Audit/research export only. Outcomes are separate from entry features. Gains, losses, flat and unevaluated reviews are retained. This manually selected quote research is neither trade P/L nor validated training data.'}


def _csv_cell(value):
    if value is None:return ''
    if isinstance(value,bool):return 'true' if value else 'false'
    # Prefix untrusted textual spreadsheet formulas, preserving actual numeric
    # negative returns as numbers. Quoting alone does not prevent CSV formulas.
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    if isinstance(value,str) and value.startswith(('\t','\r','\n')):return "'"+value
    return value


def dataset_csv(data):
    fields=['dataset_version','export_total_count','export_window_count','export_has_more',
            'request_id','symbol','submitted_at','completed_at','review_status','grade_version','grade','score','signal','heuristic_confidence',
            *FEATURE_KEYS,'entry_reference_price','entry_market_timestamp','entry_reference_fresh',
            'forward_kind','forward_market_timestamp','forward_observed_price','forward_elapsed_seconds','forward_return_percent',
            'training_eligible','training_exclusions']
    target=io.StringIO(newline='');writer=csv.DictWriter(target,fieldnames=fields);writer.writeheader()
    for row in data['rows']:
        features=row['entry_features'] or {};reference=row['entry_reference'] or {};outcome=row['future_outcome'] or {}
        values={'dataset_version':data['version'],'export_total_count':data['total_count'],
                'export_window_count':data['window_count'],'export_has_more':data['has_more'],
                **{k:row[k] for k in ('request_id','symbol','submitted_at','completed_at','review_status')},
                'grade_version':features.get('version'),**{k:features.get(k) for k in ('grade','score','signal','heuristic_confidence',*FEATURE_KEYS)},
                'entry_reference_price':reference.get('price'),'entry_market_timestamp':reference.get('market_timestamp'),
                'entry_reference_fresh':reference.get('fresh'),'forward_kind':outcome.get('kind'),
                'forward_market_timestamp':outcome.get('market_timestamp'),'forward_observed_price':outcome.get('observed_price'),
                'forward_elapsed_seconds':outcome.get('elapsed_seconds'),'forward_return_percent':outcome.get('return_percent'),
                'training_eligible':False,'training_exclusions':';'.join(row['training_exclusions'])}
        writer.writerow({k:_csv_cell(v) for k,v in values.items()})
    return target.getvalue()


def build_research_dataset_router(*,require_session):
    router=APIRouter()
    @router.get('/auto-trader/pro-ticker/manual-signals/dataset')
    def dataset(request:Request):
        require_session(request)
        format=request.query_params.get('format','json')
        if format not in ('json','csv'):raise HTTPException(422,'Expected json or csv.')
        if set(request.query_params)-{'format'}:raise HTTPException(422,'Unsupported dataset query.')
        try:
            data=research_dataset()
            if format=='json':
                from fastapi.responses import JSONResponse
                return JSONResponse(data,headers={'Cache-Control':'no-store'})
            return Response(dataset_csv(data),media_type='text/csv',headers={
                'Content-Disposition':'attachment; filename="pro-ticker-research-evidence.csv"','Cache-Control':'no-store',
                'X-Research-Total-Count':str(data['total_count']),'X-Research-Window-Count':str(data['window_count']),
                'X-Research-Has-More':'true' if data['has_more'] else 'false'})
        except Exception:raise HTTPException(503,'Research dataset unavailable.') from None
    return router
