"""Read-only reporting; never writes the trading ledger."""
import json, time
DAY=86400000

def daily(db,s,now=None):
    now=now or int(time.time()*1000)
    ts=s.get('updated_at',0)
    current=s.get('metrics',{}).get('equity')
    cutoff=now-DAY
    result={'net':None,'since':None,'as_of':ts,'partial':s['started_at']>cutoff,'stale':now-ts>45000}
    if current is None:return result
    if result['partial']:
        baseline=s['capital'];result['since']=s['started_at']
    else:
        row=db.execute('select ts,body from samples where ts<=? order by ts desc limit 1',(cutoff,)).fetchone()
        if not row or cutoff-row[0]>60000:
            result['reason']='Нет наблюдения у границы 24 часов';return result
        baseline=json.loads(row[1]).get('metrics',{}).get('equity')
        if baseline is None:return result
        result['since']=row[0]
    result['net']=current-baseline
    return result

def summary(db,s,now=None):
    now=now or int(time.time()*1000);d=daily(db,s,now);m=s.get('metrics',{})
    def money(v):return 'нет данных' if v is None else f'{v:+.2f} USDT'
    rows=db.execute('select kind,body from events where ts>?',(now-3600000,)).fetchall()
    fills=[json.loads(b) for k,b in rows if k=='future_fill' and json.loads(b).get('reason','').startswith('part_')]
    exits=sum(x['reason'].endswith('_exit') for x in fills)
    entries=sum(x['reason'].endswith('_entry') for x in fills)
    title='с запуска (<24ч)' if d['partial'] else '24ч'
    parts=[p for p in s.get('parts',[]) if p['active']]
    intraday=sum(p['side']*p['qty']*(m.get('future_mark',p['entry'])-p['entry']) for p in parts)
    stamp=time.strftime('%d.%m %H:%M',time.gmtime(now/1000+10800))
    lines=[f'Коровин BTC · ВИРТУАЛЬНО · {stamp} МСК',f'Чистыми за {title}: {money(d["net"])}',f'Общий результат: {money(m.get("net"))}',f'За час: входов {entries}, выходов {exits}',f'Открыто частей: {len(parts)}; их P/L до расходов: {money(intraday)}',f'Опцион: {s.get("option_qty",0)} BTC; P/L {money(m.get("option_open"))}',f'Фьючерс: {s.get("future_qty",0):.4f} BTC; P/L {money(m.get("future_open"))}',f'Комиссии всего: {s.get("fees",0):.2f}; funding всего: {money(s.get("funding"))}',f'Пропусков котировок за опыт: {s.get("gap_count",0)}. Суточный итог включает открытый P/L.']
    if d.get('reason'):lines.append(d['reason'])
    if d['stale'] or s.get('last_error'):lines.append('ВНИМАНИЕ: котировки устарели или ошибка сервиса; оценки не текущие.')
    return '\n'.join(lines)
