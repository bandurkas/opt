import sqlite3,unittest,json
from reporting import daily
class DailyTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.execute('create table samples(ts integer,body text)')
 def test_partial_includes_all_costs_in_equity(self):
  s=dict(started_at=1000,updated_at=2000,capital=20000,metrics={'equity':19980})
  d=daily(self.db,s,2000);self.assertEqual(d['net'],-20);self.assertTrue(d['partial'])
 def test_boundary_gap_not_fabricated(self):
  s=dict(started_at=1,updated_at=100000000,capital=20000,metrics={'equity':20020})
  self.assertIsNone(daily(self.db,s,100000000)['net'])
 def test_rolling_equity_difference(self):
  now=100000000;cut=now-86400000
  self.db.execute('insert into samples values(?,?)',(cut-10000,json.dumps({'metrics':{'equity':19970}})))
  s=dict(started_at=1,updated_at=now,capital=20000,metrics={'equity':20020})
  self.assertEqual(daily(self.db,s,now)['net'],50)
if __name__=='__main__':unittest.main()
