import unittest
from unittest.mock import patch
import n6_dashboard as d
import json,sqlite3,tempfile
from pathlib import Path

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
    def test_trade_chart_anchored_to_trade(self):
        p=dict(id='test',bybit_symbol='BTCUSDT',frame='15m',entered=1700000000000,closed_at=1700003600000)
        response=dict(time=1,result=dict(list=[]))
        with patch.object(d,'read_positions',return_value=[p]),patch.object(d,'bybit',return_value=response) as api:
            d.chart('BTCUSDT','15','test')
            self.assertLess(api.call_args.kwargs['start'],p['entered'])
            self.assertGreater(api.call_args.kwargs['end'],p['closed_at'])
            with self.assertRaises(ValueError):d.chart('BTCUSDT','30','test')
            with self.assertRaises(ValueError):d.chart('BTCUSDT','15','missing')
    def test_fill_prices_come_from_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.sqlite3'
            c=sqlite3.connect(path)
            c.execute('create table n6_sim_positions(id text,payload text)')
            c.execute('create table n6_sim_events(position_id text,payload text)')
            c.execute('insert into n6_sim_positions values (?,?)',('a',json.dumps(dict(variant='A',symbol='BTC-USDT-SWAP',entered=10,stop=99))))
            for kind,at,price in [('entry',10,100),('stop',20,98.8),('funding',15,None)]:
                c.execute('insert into n6_sim_events values (?,?)',('a',json.dumps(dict(kind=kind,at=at,price=price,qty=1))))
            c.commit();c.close()
            with patch.object(d,'DB',path):
                p=d.read_positions()[0]
                self.assertEqual(len(p['fills']),2)
                self.assertEqual(p['fills'][1]['price'],98.8)
if __name__=='__main__':unittest.main()
