from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from contextlib import closing
import unittest
from unittest.mock import patch

import solana_parallel_reads as reads


class ParallelReadTests(unittest.TestCase):
    def test_fast_result_and_early_close_do_not_wait_for_slow_read(self):
        started=Event();release=Event();finished=Event()
        def read(item):
            if item=='slow':
                started.set();release.wait(3);finished.set()
            else:self.assertTrue(started.wait(2))
            return item
        with ThreadPoolExecutor(max_workers=3) as pool,patch.object(reads,'_pool',pool):
            try:
                with closing(reads.read_many(['slow','fast'],read)) as values:
                    self.assertEqual(next(values),('fast','fast'))
                self.assertFalse(finished.is_set(),'Early close waited for the slow read')
            finally:release.set()

    def test_requests_are_bounded_and_failure_does_not_discard_other_results(self):
        lock=Lock();active=0;peak=0
        def read(item):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:
                if item==4:raise ValueError('synthetic')
                return item*2
            finally:
                with lock:active-=1
        with closing(reads.read_many(range(15),read)) as values:results=dict(values)
        self.assertLessEqual(peak,3)
        self.assertEqual(results[14],28);self.assertIsInstance(results[4],ValueError)
