"""Validated read-only Jupiter builds, with shared pacing and low-priority research.

This module has no signer, transaction sender or execution-mode setting.
"""
from contextlib import contextmanager
from decimal import Decimal
from email.utils import parsedate_to_datetime
import math
from threading import Condition
import hashlib
import os
import time

import solana_http as http
from solana_real_execution import Unavailable, uint


class Deferred(Unavailable):pass
class RateLimited(Unavailable):pass


class Budget:
    def __init__(self):
        self.condition=Condition();self.identity=None;self.next_at=0;self.cooldown=0
        self.live_waiters=0;self.execution_waiters=0;self.active=0;self.last_live=float('-inf');self.rate_limits=0

    @contextmanager
    def permit(self,research=False,monitor=False):
        # Default matches Jupiter's published free-tier allowance. A paid plan
        # may explicitly configure its documented allowance; never auto-upgrade.
        try:rps=float(os.getenv('JUPITER_BUILD_RPS','1'))
        except ValueError:rps=1
        if not 1<=rps<=150:rps=1
        identity=hashlib.sha256(os.getenv('JUPITER_API_KEY','').encode()).hexdigest()
        with self.condition:
            if self.identity!=identity:
                self.identity=identity;self.next_at=self.cooldown=0
            deadline=time.monotonic()+8
            if not research:
                self.live_waiters+=1;self.last_live=time.monotonic()
                if not monitor:self.execution_waiters+=1;self.condition.notify_all()
            try:
                while True:
                    now=time.monotonic()
                    if monitor and self.execution_waiters:raise Deferred('Execution quotes have priority; monitoring deferred.')
                    if now<self.cooldown:raise RateLimited('Jupiter quote cooldown is active; no request sent.')
                    if research and (self.live_waiters or self.active or now-self.last_live<30):raise Deferred('Live quotes have priority; research deferred.')
                    delay=self.next_at-now
                    if delay<=0:break
                    if research:raise Deferred('Quote capacity reserved; research deferred.')
                    if now+delay>=deadline:raise Deferred('Quote capacity busy; retrying on the next live cycle.')
                    self.condition.wait(timeout=delay)
                self.next_at=now+1.05/rps;self.active+=1
            finally:
                if not research:
                    self.live_waiters-=1
                    if not monitor:self.execution_waiters-=1
        try:yield
        finally:
            with self.condition:
                self.active-=1;self.condition.notify_all()

    def limited(self,headers):
        raw=headers.get('Retry-After','2')
        try:delay=float(raw)
        except (TypeError,ValueError):
            try:delay=parsedate_to_datetime(raw).timestamp()-time.time()
            except (TypeError,ValueError,OverflowError):delay=2
        if not math.isfinite(delay) or delay<0:delay=2
        delay=max(1,min(delay,86400))
        with self.condition:
            self.cooldown=max(self.cooldown,time.monotonic()+delay)
            self.rate_limits+=1;self.condition.notify_all()

    def status(self):
        with self.condition:
            now=time.monotonic()
            return {'cooldown_seconds':round(max(0,self.cooldown-now),1),
                    'live_waiters':self.live_waiters,'active_requests':self.active,
                    'recent_live_request':now-self.last_live<30,'observed_rate_limits':self.rate_limits}


_budget=Budget()


def build_quote(owner,input_mint,output_mint,amount,policy,*,mode='standard',research=False):
    if mode not in ('standard','fast'):raise ValueError('Unsupported quote comparison mode.')
    if mode!='standard' and not research:raise ValueError('Fast routing is research-only.')
    api_key=os.getenv('JUPITER_API_KEY','')
    if not api_key:raise Unavailable('Jupiter API key is not configured.')
    params={'inputMint':input_mint,'outputMint':output_mint,'amount':str(uint(amount)),'taker':owner,
            'slippageBps':policy['slippage_bps'],'maxAccounts':48,'wrapAndUnwrapSol':'false',
            'platformFeeBps':0,'transactionVersion':'0'}
    if mode=='fast':params['mode']='fast'
    try:
        with _budget.permit(research=research,monitor=not research and policy.get('_quote_priority')=='monitor'):
            start=time.monotonic()
            response=http.get('https://api.jup.ag/swap/v2/build',params=params,headers={'x-api-key':api_key},timeout=3 if research else 8,allow_redirects=False)
            elapsed=(time.monotonic()-start)*1000
            if response.status_code in (401,403):raise Unavailable('Jupiter API key or plan does not permit swap building.')
            if response.status_code==429:
                _budget.limited(response.headers)
                raise RateLimited('Jupiter swap API is rate limited; no new transaction was built.')
            if response.status_code!=200 or len(response.content)>500_000 or elapsed>12000:raise ValueError()
            data=response.json()
            if data['inputMint']!=input_mint or data['outputMint']!=output_mint or uint(data['inAmount'])!=amount or data['swapMode']!='ExactIn':raise ValueError()
            out=uint(data['outAmount']);floor=uint(data['otherAmountThreshold']);impact=Decimal(str(data['priceImpactPct']))
            if not impact.is_finite() or abs(impact)*100>policy['max_price_impact_percent']:raise ValueError()
            if data['slippageBps']!=policy['slippage_bps'] or not 0<floor<=out or floor<out*(10000-policy['slippage_bps'])//10000:raise ValueError()
            if data.get('otherInstructions') or data.get('tipInstruction'):raise ValueError()
            data['_observed_at']=time.time();data['_request_ms']=round(elapsed,3)
            return data
    except Unavailable:raise
    except Exception:raise Unavailable('No supported Jupiter route within the configured slippage and price-impact limits.') from None
