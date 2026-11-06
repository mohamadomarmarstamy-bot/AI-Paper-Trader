import unittest
from unittest.mock import Mock
import solana_real_execution as ex
class BuyRequoteTests(unittest.TestCase):
    def provider(self):
        p=ex.Provider();p.build=Mock(return_value={'outAmount':1000,'otherAmountThreshold':990});p._prepare_once=Mock(side_effect=[ex.SimulationSlippage('simulation'),{'wire':'only signed once'}]);return p
    def test_one_retry_with_fresh_authorization(self):
        p=self.provider();auth=Mock();r=p.prepare('owner','buy','mint',10,{},auth)
        self.assertEqual(p.build.call_count,2);self.assertEqual(p._prepare_once.call_count,2);self.assertEqual(auth.call_count,2)
    def test_rejects_chasing_price(self):
        p=self.provider();p.build.side_effect=[{'outAmount':1000,'otherAmountThreshold':990},{'outAmount':980,'otherAmountThreshold':970}]
        with self.assertRaisesRegex(ex.Unavailable,'worsened'):p.prepare('owner','buy','mint',10,{},Mock())
        self.assertEqual(p._prepare_once.call_count,1)
    def test_no_generic_or_uncertain_retry(self):
        p=self.provider();p._prepare_once.side_effect=ex.Unavailable('unknown')
        with self.assertRaises(ex.Unavailable):p.prepare('owner','buy','mint',10,{},Mock())
        self.assertEqual(p.build.call_count,1)
    def test_second_failure_stops(self):
        p=self.provider();p._prepare_once.side_effect=ex.SimulationSlippage('again')
        with self.assertRaises(ex.SimulationSlippage):p.prepare('owner','buy','mint',10,{},Mock())
        self.assertEqual(p._prepare_once.call_count,2)
    def test_expired_authorization_prevents_requote(self):
        p=self.provider();auth=Mock(side_effect=ex.Unavailable('expired'))
        with self.assertRaises(ex.Unavailable):p.prepare('owner','buy','mint',10,{},auth)
        self.assertEqual(p.build.call_count,1)

class SellRequoteTests(unittest.TestCase):
    def test_simulation_slippage_requotes_immediately_once(self):
        p=ex.Provider();fresh={'otherAmountThreshold':123}
        p.build=Mock(return_value=fresh)
        p._prepare_once=Mock(side_effect=[ex.SimulationSlippage('slippage'),{'wire':'signed once'}])
        auth=Mock();policy={'slippage_bps':100,'minimum_sell_output_lamports':120}
        result=p.prepare('owner','sell','mint',10,policy,auth)
        self.assertEqual(result,{'wire':'signed once'})
        p.build.assert_called_once_with('owner','mint',ex.SOL,10,policy)
        self.assertEqual(p._prepare_once.call_args.args[-1],fresh)
        self.assertEqual(auth.call_count,2)

    def test_second_simulation_failure_stops_and_generic_failure_never_requotes(self):
        for error,calls in ((ex.SimulationSlippage('slip'),2),(ex.Unavailable('unknown'),1)):
            p=ex.Provider();p.build=Mock(return_value={});p._prepare_once=Mock(side_effect=error)
            with self.assertRaises(ex.Unavailable):p.prepare('owner','sell','mint',10,{},Mock())
            self.assertEqual(p._prepare_once.call_count,calls)

    def test_expired_authorization_prevents_sell_requote(self):
        p=ex.Provider();p.build=Mock();p._prepare_once=Mock(side_effect=ex.SimulationSlippage('slip'))
        with self.assertRaisesRegex(ex.Unavailable,'expired'):
            p.prepare('owner','sell','mint',10,{},Mock(side_effect=ex.Unavailable('expired')))
        p.build.assert_not_called()
