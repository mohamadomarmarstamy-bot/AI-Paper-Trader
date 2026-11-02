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
