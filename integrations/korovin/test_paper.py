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
        self.assertEqual(self.e.db.execute("select count(*) from events where kind='future_fill'").fetchone()[0],0)
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


    def test_full_close_retires_parts_without_double_counting(self):
        s=self.e.s
        part=dict(side=1,n=1,entry=99500,exit=100000,qty=.01,
                  active=True,cycles=2,gross=10.)
        s.update(phase='closing',future_qty=-.11,future_avg=100000,
                 option_qty=.24,option_entry=1000,option_symbol='test',
                 parts=[part])
        # An unavailable option close must leave both legs AND the slot intact.
        self.e.close(quote(),quote(1000,.01),1000)
        self.assertTrue(part['active'])
        self.assertEqual(self.e.db.execute('select count(*) from events').fetchone()[0],0)
        expected=self.e.metrics(quote(),quote(1000))['liquidation_net']
        self.e.close(quote(),quote(1000),2000)
        self.assertEqual(s['phase'],'closed')
        self.assertFalse(part['active'])
        self.assertEqual((part['cycles'],part['gross']),(2,10.))
        self.assertEqual((part['closed_at'],part['close_reason']),(2000,'close_all'))
        self.assertAlmostEqual(self.e.metrics(quote(),quote(1000))['net'],expected)
        import json
        rows=self.e.db.execute("select body from events where kind='parts_retired'").fetchall()
        self.assertEqual(len(rows),1)
        original=json.loads(rows[0][0])['parts'][0]
        self.assertTrue(original['active'])
        self.assertEqual(original['entry'],99500)
        count=self.e.db.execute('select count(*) from events').fetchone()[0]
        self.e.close(quote(),quote(1000),3000)
        self.assertEqual(self.e.db.execute('select count(*) from events').fetchone()[0],count)
        self.e.save()
        self.e.db.close()
        self.e=paper.Engine(self.path)
        self.assertFalse(self.e.s['parts'][0]['active'])
        self.assertEqual(self.e.s['parts'][0]['closed_at'],2000)


    def agent_fixture(self, ts, previous):
        self.e.s.update(phase='running', started_at=1000, updated_at=previous,
                        option_qty=.24, option_entry=1000, option_symbol='test',
                        option_expiry=ts+30*86400000, future_qty=-.12,
                        future_avg=100000, base_hedge=.12, last_funding_poll=ts,
                        parts=[dict(side=1,n=1,entry=99500,exit=100000,qty=.01,
                                    active=False,cycles=0,gross=0)])
        def fake(path, **kw):
            return {'time':ts,'result':{'list':[quote(99000) if kw.get('category')=='linear' else quote(1000)]}}
        return fake

    def test_agent_gap_pauses_before_fill_and_resumes_next_snapshot(self):
        ts=1000000
        with patch.object(paper,'api',self.agent_fixture(ts,ts-70000)), patch.object(paper.time,'time',return_value=ts/1000):
            self.e.tick()
        self.assertEqual(self.e.s['agent']['decision'],'gap_pause')
        self.assertFalse(self.e.s['parts'][0]['active'])
        self.assertEqual(self.e.s['gap_count'],1)
        def fresh(path, **kw):
            return {'time':ts+10000,'result':{'list':[quote(99000) if kw.get('category')=='linear' else quote(1000)]}}
        with patch.object(paper,'api',fresh), patch.object(paper.time,'time',return_value=(ts+10000)/1000):
            self.e.tick()
        self.assertTrue(self.e.s['parts'][0]['active'])
        self.assertEqual(self.e.s['agent']['decision'],'grid_fills')
        self.assertEqual(self.e.db.execute("select count(*) from events where kind='agent_version'").fetchone()[0],1)

    def test_agent_stale_quote_no_fill_and_preserves_positions(self):
        ts=1000000
        with patch.object(paper,'api',self.agent_fixture(ts,ts-10000)), patch.object(paper.time,'time',return_value=(ts+31000)/1000):
            self.e.tick()
        self.assertEqual(self.e.s['agent']['decision'],'error_pause')
        self.assertEqual(self.e.s['future_qty'],-.12)
        self.assertFalse(self.e.s['parts'][0]['active'])
        self.assertEqual(self.e.s['updated_at'],ts-10000)

    def test_agent_gap_does_not_block_risk_close(self):
        ts=1000000
        fake=self.agent_fixture(ts,ts-70000)
        self.e.s['option_expiry']=ts+86400000
        with patch.object(paper,'api',fake), patch.object(paper.time,'time',return_value=ts/1000):
            self.e.tick()
        self.assertEqual(self.e.s['phase'],'closed')
        self.assertEqual(self.e.s['agent']['decision'],'closed')
        self.assertEqual(self.e.s['future_qty'],0)


    def test_agent_nonincreasing_timestamp_preserves_ledger(self):
        ts=1000000
        for previous in (ts,ts+10000):
            with self.subTest(previous=previous):
                with patch.object(paper,'api',self.agent_fixture(ts,previous)), patch.object(paper.time,'time',return_value=ts/1000):
                    self.e.tick()
                self.assertEqual(self.e.s['agent']['decision'],'error_pause')
                self.assertIn('non-increasing',self.e.s['last_error'])
                self.assertEqual(self.e.s['future_qty'],-.12)
                self.assertEqual(self.e.s['updated_at'],previous)
                self.assertFalse(self.e.s['parts'][0]['active'])
                self.assertEqual(self.e.db.execute("select count(*) from events where kind='future_fill'").fetchone()[0],0)

    def test_agent_quote_expires_during_funding_rolls_back(self):
        ts=1000000
        fake=self.agent_fixture(ts,ts-10000)
        self.e.s['last_funding_poll']=0
        clock=[ts/1000]
        def slow_funding(now):
            self.e.s['funding']+=2.0
            self.e.s['funding_done'].append(now)
            self.e.event(now,'funding',cash=2.0)
            clock[0]=(ts+31000)/1000
        with patch.object(paper,'api',fake), patch.object(paper.time,'time',side_effect=lambda:clock[0]), patch.object(self.e,'funding',side_effect=slow_funding):
            self.e.tick()
        self.assertEqual(self.e.s['agent']['decision'],'error_pause')
        self.assertIn('expired during data collection',self.e.s['last_error'])
        self.assertEqual(self.e.s['future_qty'],-.12)
        self.assertEqual(self.e.s['option_qty'],.24)
        self.assertEqual(self.e.s['updated_at'],ts-10000)
        self.assertEqual(self.e.s['funding'],0)
        self.assertEqual(self.e.s['funding_done'],[])
        self.assertEqual(self.e.s['last_funding_poll'],0)
        self.assertFalse(self.e.s['parts'][0]['active'])
        self.assertEqual(self.e.db.execute("select count(*) from events where kind in ('future_fill','funding')").fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
