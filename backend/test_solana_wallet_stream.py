import asyncio
from collections import OrderedDict
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import solana_wallet_stream as stream
from solana_copy_rpc import PROGRAMS


def notification():
    return {'method':'logsNotification','params':{'subscription':7,'result':{'context':{'slot':123},
            'value':{'signature':'1'*64,'err':None,'logs':[f'Program {next(iter(PROGRAMS))} invoke [1]']}}}}


class StreamParsingTests(unittest.TestCase):
    def test_only_acknowledged_successful_supported_swap_logs_wake_http_reads(self):
        row=notification()
        self.assertEqual(stream.notification_wallet(row,{7:'wallet'}),('wallet','1'*64))
        for change in ('unknown','failed','transfer','bad_signature','missing','wrong_type'):
            bad=deepcopy(row)
            if change=='unknown':bad['params']['subscription']=8
            elif change=='failed':bad['params']['result']['value']['err']={'InstructionError':[0,'Custom']}
            elif change=='transfer':bad['params']['result']['value']['logs']=['Program 11111111111111111111111111111111 invoke [1]']
            elif change=='bad_signature':bad['params']['result']['value']['signature']='not a signature'
            elif change=='wrong_type':bad['params']['subscription']=True
            else:del bad['params']['result']['value']['err']
            with self.subTest(change=change):self.assertIsNone(stream.notification_wallet(bad,{7:'wallet'}))

    def test_acknowledge_before_read_preserves_notifications_received_during_read(self):
        wake=stream.Wakeup();wake.mark(['wallet']);wake.acknowledge(['wallet'])
        wake.mark(['wallet'])
        self.assertEqual(wake.pending(),{'wallet'})
        wake.retain([]);self.assertFalse(wake.pending())

    def test_public_status_never_contains_transport_credentials(self):
        self.assertTrue(stream.public()['polling_backup'])
        self.assertNotIn('url',stream.public())

    def test_paused_entries_keep_held_sources_and_exclude_paper_only_candidates(self):
        source={'config':{'enabled':True},'traders':[{'wallet':'live','active':True},
                 {'wallet':'research','active':True,'paper_only':True}],
                'cursors':{w:{'provider':'rpc'} for w in ('live','research','held')}}
        run={'enabled':False,'positions':{'p':{'source_wallet':'held'}}}
        with patch('solana_copy_trader.load',return_value=(source,0)),patch('solana_real_worker.load',return_value=(run,0)):
            self.assertEqual(stream.target_wallets(),('held',))
            run['enabled']=True
            self.assertEqual(set(stream.target_wallets()),{'live','held'})


class FakeSocket:
    def __init__(self):
        self.received=asyncio.Queue();self.sent=[];self.subscribed=asyncio.Event()
    async def send(self,raw):
        self.sent.append(json.loads(raw));self.subscribed.set()
    async def recv(self):return await self.received.get()


class StreamAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscriptions_reconcile_http_and_duplicate_notifications_are_coalesced(self):
        socket=FakeSocket();marks=[];changed=asyncio.Event();seen=OrderedDict()
        def mark(wallets):marks.append(list(wallets));changed.set()
        with patch.object(stream.wakeup,'mark',side_effect=mark):
            task=asyncio.create_task(stream.session(socket,('wallet',),loader=lambda:('wallet',),seen=seen))
            try:
                await asyncio.wait_for(socket.subscribed.wait(),1)
                request=socket.sent[0]
                self.assertEqual(request['method'],'logsSubscribe')
                self.assertEqual(request['params'],[{'mentions':['wallet']},{'commitment':'confirmed'}])
                await socket.received.put(json.dumps({'id':1,'result':7}))
                await asyncio.wait_for(changed.wait(),1)
                self.assertEqual(marks,[['wallet']])  # HTTP reconciliation after subscribe.
                changed.clear();await socket.received.put(json.dumps(notification()))
                await asyncio.wait_for(changed.wait(),1)
                self.assertEqual(len(marks),2)
                # Seen signatures persist across reconnects as well as within a session.
                self.assertIn(('wallet','1'*64),seen)
                changed.clear();await socket.received.put(json.dumps(notification()))
                await asyncio.sleep(.01)
                self.assertEqual(len(marks),2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task

    async def test_rejected_subscription_does_not_claim_connected(self):
        socket=FakeSocket()
        await socket.received.put(json.dumps({'id':1,'error':{'code':-32601}}))
        with self.assertRaisesRegex(ValueError,'not accepted'):
            await stream.session(socket,('wallet',),loader=lambda:('wallet',))

    async def test_wakeup_interrupts_idle_wait_and_no_redirects_are_followed(self):
        wake=stream.Wakeup();wake.bind()
        waiter=asyncio.create_task(wake.wait(60));wake.mark(['wallet'])
        await asyncio.wait_for(waiter,1)
        error=ValueError('synthetic redirect')
        self.assertIs(stream.connect_socket('wss://example.test').process_redirect(error),error)

    async def test_transport_failure_is_redacted_and_never_reaches_a_signer(self):
        def fail(url):raise ValueError('https://provider.test/?secret=do-not-log')
        async def stop(delay):
            self.assertEqual(delay,1)
            raise asyncio.CancelledError()
        with patch.object(stream.asyncio,'sleep',side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                await stream.worker(loader=lambda:('wallet',),connector=fail)
        public=stream.public()
        self.assertFalse(public['connected']);self.assertEqual(public['subscriptions'],0)
        self.assertNotIn('do-not-log',json.dumps(public))
        self.assertIn('polling remains active',public['error'])
