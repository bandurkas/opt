import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import paper


def quote(price=100000, size=1):
    return dict(bid1Price=str(price), ask1Price=str(price+1), bid1Size=str(size),
                ask1Size=str(size), markPrice=str(price+.5), indexPrice=str(price), delta='.5')


class Accounting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name)/'paper.sqlite')
        self.e = paper.Engine(self.path)

    def tearDown(self):
        self.e.db.close()
        self.tmp.cleanup()

    def test_short_close_and_reversal(self):
        q,a,r=paper.trade_position(-.12,100000,.02,99000)
        self.assertAlmostEqual(q,-.10)
        self.assertEqual(a,100000)
        self.assertAlmostEqual(r,20)
        q,a,r=paper.trade_position(-.01,100000,.02,101000)
        self.assertAlmostEqual(q,.01)
        self.assertEqual(a,101000)
        self.assertAlmostEqual(r,-10)

    def test_option_fee_cap(self):
        self.assertAlmostEqual(paper.option_fee(100000,10,.24),.168)
        self.assertAlmostEqual(paper.option_fee(100000,1000,.24),7.2)

    def test_opening_equity_includes_spread_and_fees(self):
        s=self.e.s
        s.update(option_qty=.24,option_entry=1001,future_qty=-.12,future_avg=100000,fees=14)
        m=self.e.metrics(quote(),quote(1000))
        self.assertAlmostEqual(m['net'], -.12-.06-14)
        self.assertLess(m['liquidation_net'],m['net'])

    def test_funding_before_event_and_idempotent_restart(self):
        s=self.e.s
        s.update(started_at=1000,funding_checked_to=1000,expected_funding=60000)
        self.e.future_fill(1000,-.12,100000,'initial')
        self.e.future_fill(60000,.02,100000,'same_timestamp')
        def fake(path,**kw):
            if path=='funding/history':
                return {'result':{'list':[{'fundingRateTimestamp':'60000','fundingRate':'.0001'}]}}
            return {'result':{'list':[['60000','100000','100000','100000','100000']]}}
        with patch.object(paper,'api',fake):
            self.e.funding(120000)
            self.assertAlmostEqual(s['funding'],1.2)
            self.e.save()
            self.e.db.close()
            self.e=paper.Engine(self.path)
            self.e.funding(180000)
            self.assertAlmostEqual(self.e.s['funding'],1.2)

    def test_missing_rate_is_pending(self):
        self.e.s.update(started_at=1000,funding_checked_to=1000,expected_funding=60000)
        with patch.object(paper,'api',return_value={'result':{'list':[]}}):
            self.e.funding(120000)
        self.assertTrue(self.e.s['funding_pending'])
        self.assertEqual(self.e.s['funding_checked_to'],1000)

    def test_grid_book_size_not_reused(self):
        s=self.e.s
        s.update(base_hedge=.12,future_qty=-.12,future_avg=100000,
                 parts=[dict(side=1,n=n,entry=99500,exit=100000,qty=.01,active=False,cycles=0,gross=0) for n in (1,2)])
        self.e.grid(quote(99000,.015),2000)
        self.assertEqual(sum(p['active'] for p in s['parts']),1)
        self.assertAlmostEqual(s['future_qty'],-.11)

    def test_tick_rolls_back_partial_fills(self):
        def broken():
            self.e.future_fill(1000,-.12,100000,'bad')
            raise ValueError('bad quote')
        with patch.object(self.e,'step',broken):
            self.e.tick()
        self.assertEqual(self.e.s['future_qty'],0)
        self.assertEqual(self.e.db.execute('select count(*) from events').fetchone()[0],0)
        self.assertIn('bad quote',self.e.s['last_error'])

    def test_no_private_endpoint(self):
        with self.assertRaises(ValueError):
            paper.api('order/create')

    def test_close_requires_both_legs(self):
        s=self.e.s
        s.update(phase='closing',future_qty=-.12,future_avg=100000,option_qty=.24,option_entry=1000)
        self.e.close(quote(),quote(1000,.01),1000)
        self.assertEqual(s['future_qty'],-.12)
        self.assertEqual(s['option_qty'],.24)


if __name__=='__main__':
    unittest.main()
