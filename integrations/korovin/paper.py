"""Isolated, public GET-only forward paper experiment. Python stdlib only."""
import argparse
import hashlib
import json
import math
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = 'KOR-BTC-PAPER-001'
CONFIG = dict(capital=20000., option_qty=.24, part_qty=.01, width=.0045,
              offsets=[1, 2, 5, 12], perp_fee=.00055, option_fee=.0003,
              option_cap=.07, loss_floor=16000., min_days=21, max_days=35)
RULE_HASH = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()


def api(path, **params):
    if path not in ('tickers', 'instruments-info', 'funding/history', 'mark-price-kline', 'kline'):
        raise ValueError('Public market endpoint not allowed')
    url = 'https://api.bybit.com/v5/market/' + path + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=12) as r:
        d = json.load(r)
    if d.get('retCode') != 0:
        raise ValueError(str(d.get('retMsg')))
    if abs(time.time()*1000 - int(d['time'])) > 30000:
        raise ValueError('Exchange clock stale / local clock out of sync')
    return d


def num(d, k):
    x = float(d.get(k) or 0)
    if not math.isfinite(x):
        raise ValueError('Non-finite ' + k)
    return x


def rounded(value, step, up=False):
    return float((Decimal(str(value))/Decimal(str(step))).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_HALF_UP)*Decimal(str(step)))


def valid_quote(q):
    b, a = num(q, 'bid1Price'), num(q, 'ask1Price')
    return 0 < b <= a and num(q, 'markPrice') > 0


def available(q, side, qty):
    return valid_quote(q) and num(q, 'ask1Size' if side > 0 else 'bid1Size') >= qty


def option_fee(index, premium, qty):
    return min(CONFIG['option_fee']*index, CONFIG['option_cap']*premium)*abs(qty)


def trade_position(qty, avg, change, price):
    """Linear futures realization; entry notional never credited as profit."""
    if qty == 0 or qty*change > 0:
        new = qty+change
        return new, (abs(qty)*avg+abs(change)*price)/abs(new), 0.
    closed = min(abs(qty), abs(change))
    realized = closed*(price-avg)*(1 if qty > 0 else -1)
    new = qty+change
    if abs(new) < 1e-10:
        return 0., 0., realized
    return new, price if qty*new < 0 else avg, realized


def initial_state():
    return dict(version=VERSION, rules_hash=RULE_HASH, config=CONFIG,
                phase='waiting', reason='Ожидание ликвидной конструкции',
                capital=CONFIG['capital'], fees=0., funding=0., realized=0.,
                future_qty=0., future_avg=0., option_qty=0., option_entry=0.,
                option_realized=0., parts=[], funding_done=[], funding_pending=False,
                funding_checked_to=0, updated_at=0, started_at=None,
                gap_count=0, last_error=None, peak=CONFIG['capital'], drawdown=0.)


class Engine:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute('pragma journal_mode=WAL')
        self.db.executescript('''
        create table if not exists state(id integer primary key, body text);
        create table if not exists events(id integer primary key, ts integer, kind text, body text);
        create table if not exists samples(ts integer primary key, body text);
        ''')
        row = self.db.execute('select body from state where id=1').fetchone()
        self.s = json.loads(row[0]) if row else initial_state()
        if self.s['rules_hash'] != RULE_HASH:
            raise ValueError('Rules changed: use a new ledger')
        self.lock = threading.RLock()

    def event(self, ts, kind, **data):
        self.db.execute('insert into events(ts,kind,body) values(?,?,?)',
                        (ts, kind, json.dumps(data, ensure_ascii=False)))

    def save(self):
        self.db.execute('insert or replace into state values(1,?)',
                        (json.dumps(self.s, ensure_ascii=False),))
        self.db.commit()

    def future_fill(self, ts, change, price, reason):
        s = self.s
        qty, avg, realized = trade_position(s['future_qty'], s['future_avg'], change, price)
        fee = abs(change)*price*CONFIG['perp_fee']
        s.update(future_qty=qty, future_avg=avg)
        s['realized'] += realized
        s['fees'] += fee
        self.event(ts, 'future_fill', change=change, price=price, qty_after=qty,
                   realized=realized, fee=fee, reason=reason)

    def assemble(self, f, options, instruments, fm, ts):
        s = self.s
        candidates = []
        for i in instruments:
            q = options.get(i['symbol'])
            days = (int(i['deliveryTime'])-ts)/86400000
            if (i['status'] != 'Trading' or i['optionsType'] != 'Call'
                or i['settleCoin'] != 'USDT' or i['quoteCoin'] != 'USDT'
                or not CONFIG['min_days'] <= days <= CONFIG['max_days'] or not q):
                continue
            oq = CONFIG['option_qty']
            step = float(i['lotSizeFilter']['qtyStep'])
            if abs(rounded(oq, step)-oq) > 1e-9 or oq < float(i['lotSizeFilter']['minOrderQty']):
                continue
            if not available(q, 1, oq):
                continue
            bid, ask = num(q, 'bid1Price'), num(q, 'ask1Price')
            if (ask-bid)/((ask+bid)/2) > .1:
                continue
            delta = num(q, 'delta')
            if not .35 <= delta <= .65:
                continue
            strike = float(i['symbol'].split('-')[2])
            candidates.append((abs(days-28), abs(strike-num(f, 'markPrice')), i, q))
        if not candidates:
            s['reason'] = 'Нет ликвидного USDT call 21–35 дней: ожидание без сделок'
            return
        _, _, i, q = min(candidates, key=lambda x: (x[0], x[1]))
        oq = CONFIG['option_qty']
        fs = float(fm['lotSizeFilter']['qtyStep'])
        hedge = rounded(oq*num(q, 'delta'), fs)
        part = CONFIG['part_qty']
        if (not .08 <= hedge <= .16 or abs(rounded(part, fs)-part) > 1e-9
            or part < float(fm['lotSizeFilter']['minOrderQty'])
            or part*num(f, 'bid1Price') < float(fm['lotSizeFilter'].get('minNotionalValue', 0))
            or not available(f, -1, hedge)):
            s['reason'] = 'Недостаточный объём или некорректный шаг фьючерса'
            return
        entry = num(q, 'ask1Price')
        fee = option_fee(num(q, 'indexPrice'), entry, oq)
        reserve = entry*oq + (hedge+4*part)*num(f, 'ask1Price')
        if entry*oq+fee > 2000 or reserve > s['capital']:
            s['reason'] = 'Бюджет премии/резерв не допускает сборку'
            return
        s.update(phase='running', reason='Виртуальная конструкция открыта', started_at=ts,
                 option_symbol=i['symbol'], option_qty=oq, option_entry=entry,
                 option_expiry=int(i['deliveryTime']), option_instrument=i,
                 base_hedge=hedge, future_instrument=fm, reserve=reserve)
        s['fees'] += fee
        self.event(ts, 'option_fill', symbol=i['symbol'], change=oq, price=entry, fee=fee,
                   reason='base_entry')
        self.future_fill(ts, -hedge, num(f, 'bid1Price'), 'base_entry')
        center = num(f, 'bid1Price')
        tick = float(fm['priceFilter']['tickSize'])
        width = rounded(center*CONFIG['width'], tick, True)
        s.update(center=center, width=width, funding_checked_to=ts)
        s['parts'] = [dict(side=side, n=n+1, entry=rounded(center-side*offset*width, tick),
                           exit=rounded(center-side*(offset-1)*width, tick), active=False,
                           qty=part, cycles=0, gross=0.)
                      for side in (1, -1) for n, offset in enumerate(CONFIG['offsets'])]

    def grid(self, f, ts):
        s = self.s
        f = dict(f)
        for p in s['parts']:
            side = -p['side'] if p['active'] else p['side']
            target = p['exit'] if p['active'] else p['entry']
            market = num(f, 'ask1Price' if side > 0 else 'bid1Price')
            if not ((side > 0 and market <= target) or (side < 0 and market >= target)):
                continue
            if not available(f, side, p['qty']):
                continue
            after = s['future_qty']+side*p['qty']
            if not -s['base_hedge']-.04-1e-9 <= after <= -s['base_hedge']+.04+1e-9:
                raise ValueError('Position limit invariant failed')
            self.future_fill(ts, side*p['qty'], target,
                             'part_%s_%s_%s' % (p['side'], p['n'], 'exit' if p['active'] else 'entry'))
            size_key = 'ask1Size' if side > 0 else 'bid1Size'
            f[size_key] = num(f, size_key)-p['qty']
            if p['active']:
                p['cycles'] += 1
                p['gross'] += p['qty']*(p['exit']-p['entry'])*p['side']
            p['active'] = not p['active']

    def funding(self, now):
        s = self.s
        if not s['started_at']:
            return
        # Query an overlapping window for delayed publication; paginate backwards.
        end = now
        start = max(s['started_at'], s['funding_checked_to']-86400000)
        rates = []
        while end > start:
            d = api('funding/history', category='linear', symbol='BTCUSDT',
                    startTime=start, endTime=end, limit=200)['result']['list']
            rates += d
            if len(d) < 200:
                break
            end = min(int(x['fundingRateTimestamp']) for x in d)-1
        pending = False
        for row in sorted(rates, key=lambda r: int(r['fundingRateTimestamp'])):
            ts = int(row['fundingRateTimestamp'])
            if ts <= s['started_at'] or ts in s['funding_done']:
                continue
            found = self.db.execute("select body from events where kind='future_fill' and ts<? order by ts desc,id desc limit 1", (ts,)).fetchone()
            qty = json.loads(found[0])['qty_after'] if found else 0.
            if abs(qty) < 1e-12:
                s['funding_done'].append(ts)
                continue
            k = api('mark-price-kline', category='linear', symbol='BTCUSDT', interval='1',
                    start=ts, end=ts+59999, limit=1)['result']['list']
            if not k or int(k[0][0]) != ts:
                pending = True
                continue
            price = float(k[0][1])
            rate = float(row['fundingRate'])
            cash = -qty*price*rate
            s['funding'] += cash
            s['funding_done'].append(ts)
            self.event(ts, 'funding', qty=qty, rate=rate, mark_estimate=price, cash=cash,
                       precision='1m_mark_open')
        due = s.get('expected_funding', 0)
        if due and now >= due and due not in s['funding_done']:
            pending = True
        s['funding_pending'] = pending
        if not pending:
            s['funding_checked_to'] = now

    def metrics(self, f, q):
        s = self.s
        oq = s['option_qty']
        fq = s['future_qty']
        mark_f = num(f, 'markPrice')
        mark_o = num(q, 'markPrice') if oq else 0.
        option_open = oq*(mark_o-s['option_entry'])
        future_open = fq*(mark_f-s['future_avg'])
        net = s['realized']+s['option_realized']+option_open+future_open-s['fees']+s['funding']
        close_f = num(f, 'bid1Price' if fq > 0 else 'ask1Price')
        close_o = num(q, 'bid1Price') if oq else 0.
        closing_fee = abs(fq)*close_f*CONFIG['perp_fee']
        if oq:
            closing_fee += option_fee(num(q, 'indexPrice'), close_o, oq)
        liq_net = (s['realized']+s['option_realized']+fq*(close_f-s['future_avg'])
                   +oq*(close_o-s['option_entry'])-s['fees']+s['funding']-closing_fee)
        return dict(net=net, equity=s['capital']+net, option_open=option_open,
                    future_open=future_open, liquidation_net=liq_net,
                    closing_fee_estimate=closing_fee,
                    delta=fq+oq*num(q, 'delta'), future_mark=mark_f,
                    option_mark=mark_o, future_bid=num(f, 'bid1Price'),
                    future_ask=num(f, 'ask1Price'), option_bid=close_o,
                    option_ask=num(q, 'ask1Price') if oq else 0.,
                    next_funding=int(f.get('nextFundingTime') or 0),
                    indicative_funding_rate=num(f, 'fundingRate'))

    def close(self, f, q, ts):
        s = self.s
        # Atomic simulated close only when BOTH quotes have enough size.
        fq, oq = s['future_qty'], s['option_qty']
        if (fq and not available(f, -1 if fq > 0 else 1, abs(fq))) or (oq and not available(q, -1, oq)):
            s['reason'] = 'Закрытие ожидает доступный объём bid/ask'
            return
        if fq:
            self.future_fill(ts, -fq, num(f, 'bid1Price' if fq > 0 else 'ask1Price'), 'close_all')
        if oq:
            price = num(q, 'bid1Price')
            fee = option_fee(num(q, 'indexPrice'), price, oq)
            s['option_realized'] += oq*(price-s['option_entry'])
            s['fees'] += fee
            self.event(ts, 'option_fill', symbol=s['option_symbol'], change=-oq, price=price,
                       fee=fee, reason='close_all')
            s['option_qty'] = 0.
        s.update(phase='closed', reason='Эксперимент закрыт; автоматического повторного входа нет')

    def step(self):
        s = self.s
        fd = api('tickers', category='linear', symbol='BTCUSDT')
        f = fd['result']['list'][0]
        ts = int(fd['time'])
        if not valid_quote(f):
            raise ValueError('Invalid BTCUSDT quote')
        q = {}
        if s['phase'] == 'waiting':
            ins = []
            cursor = ''
            while True:
                d = api('instruments-info', category='option', baseCoin='BTC', limit=1000, cursor=cursor)
                ins += d['result']['list']
                cursor = d['result'].get('nextPageCursor', '')
                if not cursor:
                    break
            opts = api('tickers', category='option', baseCoin='BTC')
            quotes = {x['symbol']: x for x in opts['result']['list']}
            fm = api('instruments-info', category='linear', symbol='BTCUSDT')['result']['list'][0]
            if abs(int(opts['time'])-ts) > 10000:
                raise ValueError('Assembly quotes not synchronized')
            self.assemble(f, quotes, ins, fm, ts)
            q = quotes.get(s.get('option_symbol'), {})
        elif s['option_qty']:
            od = api('tickers', category='option', symbol=s['option_symbol'])
            if not od['result']['list']:
                raise ValueError('Option unavailable; retain open position, manual review required')
            q = od['result']['list'][0]
            if abs(int(od['time'])-ts) > 10000 or not valid_quote(q):
                raise ValueError('Stale/invalid option quote; trading frozen')
        if ts-s.get('last_funding_poll', 0) > 60000:
            self.funding(ts)
            s['last_funding_poll'] = ts
        next_funding = int(f.get('nextFundingTime') or 0)
        if not s.get('expected_funding') or s['expected_funding'] in s['funding_done']:
            s['expected_funding'] = next_funding
        if s['started_at']:
            m = self.metrics(f, q)
            if s['phase'] == 'running':
                if m['equity'] <= CONFIG['loss_floor'] or ts >= s['option_expiry']-5*86400000:
                    s['phase'] = 'closing'
                elif not s['funding_pending']:
                    self.grid(f, ts)
            if s['phase'] == 'closing':
                self.close(f, q, ts)
            m = self.metrics(f, q)
            s['metrics'] = m
            s['peak'] = max(s['peak'], m['equity'])
            s['drawdown'] = max(s['drawdown'], s['peak']-m['equity'])
        if s['updated_at'] and ts-s['updated_at'] > 60000:
            s['gap_count'] += 1
            self.event(ts, 'data_gap', previous=s['updated_at'], seconds=(ts-s['updated_at'])/1000)
        s.update(updated_at=ts, last_error=None)
        self.db.execute('insert or replace into samples values(?,?)',
                        (ts, json.dumps(dict(future=f, option=q, metrics=s.get('metrics')))))
        self.save()

    def tick(self):
        with self.lock:
            before = json.dumps(self.s)
            try:
                self.step()
            except Exception as e:
                self.db.rollback()
                self.s = json.loads(before)
                self.s['last_error'] = type(e).__name__+': '+str(e)
                self.s['error_at'] = int(time.time()*1000)
                self.save()

    def view(self):
        with self.lock:
            d = json.loads(json.dumps(self.s))
            from reporting import daily
            d['daily'] = daily(self.db, self.s)
            d['server_time'] = int(time.time()*1000)
            d['stale'] = d['server_time']-d['updated_at'] > 45000
            d['events'] = [dict(ts=t, kind=k, **json.loads(b)) for t,k,b in self.db.execute(
                'select ts,kind,body from events order by id desc limit 80')]
            rows = self.db.execute('select ts,body from samples order by ts desc limit 720').fetchall()
            d['curve'] = [dict(ts=t, equity=json.loads(b)['metrics']['equity'], price=json.loads(b)['metrics']['future_mark'])
                          for t,b in reversed(rows) if json.loads(b).get('metrics')]
            return d


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', default='paper.sqlite')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8107)
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    # Separate process lock prevents accidentally duplicating the writer.
    lockfile = open(args.db+'.lock', 'a')
    import os
    if os.name == 'nt':
        import msvcrt
        lockfile.seek(0)
        if not lockfile.readable():
            lockfile.write('0')
            lockfile.flush()
        lockfile.seek(0)
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    engine = Engine(args.db)
    if args.once:
        engine.tick()
        print(json.dumps(engine.view(), ensure_ascii=False))
        return
    def worker():
        while True:
            engine.tick()
            time.sleep(10)
    threading.Thread(target=worker, daemon=True).start()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == '/candles':
                frame = urllib.parse.parse_qs(parsed.query).get('frame', ['5'])[0]
                if frame not in ('1', '5', '15', '30', '60'):
                    self.send_error(400)
                    return
                try:
                    result = api('kline', category='linear', symbol='BTCUSDT', interval=frame, limit=500)
                    candles = [dict(time=int(r[0])//1000, open=float(r[1]), high=float(r[2]),
                                    low=float(r[3]), close=float(r[4])) for r in reversed(result['result']['list'])]
                    body = json.dumps(dict(candles=candles, time=result['time'], frame=frame)).encode()
                    self.send_response(200)
                except Exception:
                    body = json.dumps(dict(error='Свечи Bybit временно недоступны')).encode()
                    self.send_response(503)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/lightweight-charts.js':
                body = Path(__file__).with_name('lightweight-charts.js').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/':
                html = Path(__file__).with_name('local.html')
                if html.exists():
                    body = html.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            if self.path != '/state':
                self.send_error(404)
                return
            body = json.dumps(engine.view(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
