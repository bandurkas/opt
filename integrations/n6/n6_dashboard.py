"""Read-only SIM-001/A adapter. Bybit display quotes never alter OKX accounting."""
import json
import math
import os
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request

DB=Path(__file__).parent/'state.sqlite3'
CACHE={}
LOCK=threading.Lock()

def bybit(path, **params):
    key=(path,urlencode(params))
    with LOCK:
        cached=CACHE.get(key)
        if cached and time.time()-cached[0]<15:return cached[1]
    with urlopen(Request('https://api.bybit.com/v5/market/'+path+'?'+key[1],
                        headers={'User-Agent':'n6-dashboard/1'}),timeout=8) as r:
        result=json.load(r)
    if result.get('retCode')!=0:raise ValueError('Bybit rejected market request')
    with LOCK:CACHE[key]=(time.time(),result)
    return result

def read_positions():
    c=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=5)
    try:
        events={}
        for key,raw in c.execute('SELECT position_id,payload FROM n6_sim_events'):
            e=json.loads(raw)
            if e.get('kind') in ('entry','stop','target','timeout'):
                events.setdefault(key,[]).append({k:e.get(k) for k in ('kind','at','price','qty')})
        rows=[]
        for key,raw in c.execute('SELECT id,payload FROM n6_sim_positions'):
            p=json.loads(raw)
            if p.get('variant')!='A':continue
            allowed=['symbol','frame','status','sign','avg','stop','t2','q','net','gross',
                     'fees','funding','entered','closed_at','marked_at','eligible','ambiguous']
            row={k:p.get(k) for k in allowed};row['id']=key
            row['fills']=sorted(events.get(key,[]),key=lambda e:e['at'])
            row['asset']=p['symbol'].removesuffix('-USDT-SWAP')
            row['bybit_symbol']=row['asset']+'USDT'
            rows.append(row)
        return sorted(rows,key=lambda p:p.get('entered') or p['eligible'],reverse=True)
    finally:c.close()

def positive(v):
    try:
        n=float(v)
        return n if math.isfinite(n) and n>0 else None
    except (TypeError,ValueError):return None

def state():
    rows=read_positions();quotes={};error=None;quote_at=None
    try:
        response=bybit('tickers',category='linear')
        quote_at=int(response['time'])
        quotes={q['symbol']:q for q in response['result']['list']}
    except Exception:error='Котировки Bybit недоступны; PnL симулятора не подменён.'
    for p in rows:
        q=quotes.get(p['bybit_symbol'],{})
        p['bybit_last']=positive(q.get('lastPrice'))
        p['bybit_mark']=positive(q.get('markPrice'))
        p['quote_available']=p['bybit_last'] is not None
    closed=[p for p in rows if p['status']=='closed']
    opened=[p for p in rows if p['status']=='open']
    wins=sum((p['net'] or 0)>0 for p in closed)
    return dict(at=int(time.time()*1000),quote_at=quote_at,quote_error=error,
        mode='simulation',variant='A',signal_source='OKX',pnl_source='OKX SIM-001',quote_source='Bybit linear',
        closed=len(closed),open=len(opened),wins=wins,
        losses=sum((p['net'] or 0)<0 for p in closed),
        skipped=sum(p['status']=='skipped' for p in rows),
        pending=sum(p['status']=='pending' for p in rows),
        closed_net=sum(p['net'] or 0 for p in closed),
        open_net=sum(p['net'] or 0 for p in opened),positions=rows)

def chart(symbol, frame, position_id=None):
    positions=read_positions()
    allowed={p['bybit_symbol'] for p in positions}|{'BTCUSDT'}
    if symbol not in allowed or not re.fullmatch(r'[A-Z0-9]{2,30}',symbol):raise ValueError('Unknown symbol')
    if frame not in ('15','30'):raise ValueError('Unsupported interval')
    params=dict(category='linear',symbol=symbol,interval=frame,limit=120)
    if position_id:
        p=next((p for p in positions if p['id']==position_id),None)
        if not p or p['bybit_symbol']!=symbol or p['frame']!=frame+'m' or not p.get('entered'):
            raise ValueError('Unknown executed position')
        step=int(frame)*60000
        params.update(start=max(0,(p['entered']//step-12)*step),
                      end=min(int(time.time()*1000),(p.get('closed_at') or int(time.time()*1000))+12*step),limit=1000)
    response=bybit('kline',**params)
    return dict(symbol=symbol,frame=frame,at=response['time'],source='Bybit',
        candles=[dict(time=int(r[0]),open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]))
                 for r in reversed(response['result']['list'])])

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u=urlparse(self.path);q=parse_qs(u.query)
        try:
            if u.path=='/state':payload=state()
            elif u.path=='/chart':payload=chart(q.get('symbol',['BTCUSDT'])[0],q.get('frame',['15'])[0],q.get('position',[''])[0])
            elif u.path=='/health':payload={'ok':True,'read_only':True}
            else:self.send_error(404);return
            body=json.dumps(payload,allow_nan=False).encode();self.send_response(200)
        except ValueError:
            body=b'{"error":"Invalid market request"}';self.send_response(400)
        except Exception:
            body=b'{"error":"Strategy 6 data unavailable"}';self.send_response(503)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass

if __name__=='__main__':
    ThreadingHTTPServer((os.environ.get('N6_DASH_BIND','127.0.0.1'),8106),Handler).serve_forever()
