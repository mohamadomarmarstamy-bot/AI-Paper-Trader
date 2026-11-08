"""Bounded read-only work; callers alone update the trading ledger.

Early exit cancels queued work without waiting for unrelated in-flight reads.
The shared executor bounds active requests even across successive worker cycles.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='solana-read')


def read_many(items, read):
    remaining=iter(enumerate(items));pending={}
    def submit():
        try:index,item=next(remaining)
        except StopIteration:return
        pending[_pool.submit(read,item)]=(index,item)
    try:
        for _ in range(3):submit()
        while pending:
            done,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in sorted(done,key=lambda f:pending[f][0]):
                _,item=pending.pop(future)
                try:value=future.result()
                except Exception as error:value=error
                yield item,value
                submit()
    finally:
        for future in pending:future.cancel()
