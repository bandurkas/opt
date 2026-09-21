import unittest
from unittest.mock import patch
import n6_dashboard as d

class Tests(unittest.TestCase):
    def test_null_not_zero(self):
        for v in [None,'','nan','inf','-1','0']:self.assertIsNone(d.positive(v))
    def test_no_fallback_quote(self):
        p=dict(status='open',net=25,bybit_symbol='APTUSDT')
        with patch.object(d,'read_positions',return_value=[p]),patch.object(d,'bybit',side_effect=RuntimeError):
            s=d.state();self.assertEqual(s['open_net'],25);self.assertIsNone(s['positions'][0]['bybit_last'])
    def test_quote_does_not_change_pnl(self):
        p=dict(status='closed',net=-99,bybit_symbol='APTUSDT')
        data=dict(time=1,result=dict(list=[dict(symbol='APTUSDT',lastPrice='200',markPrice='199')]))
        with patch.object(d,'read_positions',return_value=[p]),patch.object(d,'bybit',return_value=data):
            s=d.state();self.assertEqual(s['closed_net'],-99);self.assertEqual(s['positions'][0]['bybit_last'],200)
    def test_reject_arbitrary_symbol(self):
        with patch.object(d,'read_positions',return_value=[]):
            with self.assertRaises(ValueError):d.chart('http://localhost','15')
    def test_only_15_30(self):
        with patch.object(d,'read_positions',return_value=[]):
            with self.assertRaises(ValueError):d.chart('BTCUSDT','1')
if __name__=='__main__':unittest.main()
