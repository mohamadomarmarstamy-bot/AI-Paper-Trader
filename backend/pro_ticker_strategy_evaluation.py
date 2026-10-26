"""Chronological descriptive cohorts; no training or policy application path."""
from decimal import Decimal
from pro_ticker_performance_analytics import _stamp, _money, summarize_trades

VERSION='pro_ticker_strategy_evaluation_v1'


def evaluate_strategies(trades, *, split_at=None):
    split=_stamp(split_at) if split_at else None
    # Closed execution outcomes and frozen entry metadata only. Cohort is assigned
    # by first execution; trades spanning the cutoff are excluded, never leaked.
    development=[];holdout=[];spanning=0;unknown=0
    for trade in trades:
        entered=trade.get('opened_at')
        if not entered:
            unknown+=1
            continue
        entry,exit=_stamp(entered),_stamp(trade['closed_at'])
        if entry>exit:raise ValueError('Invalid trade chronology.')
        if split is None:continue
        if exit<split:development.append(trade)
        elif entry>=split:holdout.append(trade)
        else:spanning+=1
    def cohort(rows):
        summary=summarize_trades(rows)
        recommendations=[]
        for group in summary['groups']:
            if group['label']=='unattributed' or group['samples']<20:continue
            recommendations.append({'dimension':group['dimension'],'label':group['label'],'samples':group['samples'],
                                    'finding':'historical_gross_loss' if _money(group['gross_realized_usd'])<0 else 'historical_nonnegative_gross_result',
                                    'recommendation':'Review entry/exit evidence and costs before considering a separately approved strategy experiment.',
                                    'requires_explicit_approval':True,'applied':False})
        return {'samples':len(rows),'groups':summary['groups'],'review_recommendations':recommendations}
    return {'version':VERSION,'paper':True,'research_only':True,'training_enabled':False,'model_updated':False,
            'automatic_strategy_changes':False,'risk_limits_changed':False,'split_at':split_at,
            'evaluation_kind':'retrospective_execution_cohorts_not_prospective_validation',
            'development':cohort(development),'holdout':cohort(holdout),
            'split_spanning_excluded':spanning,'missing_entry_time_excluded':unknown,
            'status':'descriptive_chronological_split' if split else 'unavailable_split_required',
            'limitations':['A retrospective holdout selected after observing outcomes is not a forward test.',
                           'No model fitting, parameter search or predictive accuracy claim.',
                           'Costs are unavailable; net strategy superiority cannot be established.',
                           'Sparse/manual samples, multiple comparisons and market drift limit conclusions.',
                           'False-breakout, spread impact and optimal-exit labels require unavailable intraday paths and bid/ask evidence.']}
