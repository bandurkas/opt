"use client";
import { useEffect, useState } from "react";
import { metrics } from "./n6Metrics";
import N6InteractiveChart from "./N6InteractiveChart";
import { economicView } from "./n6Economics";

type Position = { id:string; asset:string; bybit_symbol:string; frame:string; status:string; sign:number;
  avg:number|null; stop:number|null; t2:number|null; net:number|null; marked_at:number|null;
  entered:number|null; closed_at:number|null; bybit_last:number|null; fees:number|null; funding:number|null;
  fills?:{kind:string;at:number;price:number;qty:number}[] };
type State = { at:number; quote_at:number|null; quote_error:string|null; closed:number; open:number;
  wins:number; losses:number; skipped:number; pending:number; closed_net:number; open_net:number; positions:Position[] };
type Candle = {time:number;open:number;high:number;low:number;close:number};
const money=(v:number|null)=>v==null?'—':`${v>=0?'+':'−'}$${Math.abs(v).toFixed(2)}`;
const price=(v:number|null)=>v==null?'—':v.toLocaleString('ru-RU',{maximumSignificantDigits:7});
const balance=(v:number)=>v.toLocaleString('ru-RU',{style:'currency',currency:'USD',minimumFractionDigits:2});
const reason=(kind:string)=>({entry:'ВХОД',stop:'ВЫХОД · СТОП',target:'ВЫХОД · ТЕЙК',timeout:'ВЫХОД · 24 ЧАСА'}[kind]??kind);
const stamp=(v:number|null)=>v==null?'—':new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});

export default function Strategy6Panel(){
  const [rawState,setState]=useState<State|null>(null);
  const [filtered,setFiltered]=useState(true);
  const economic=rawState?economicView(rawState.positions):null;
  const state=rawState&&economic&&filtered?{...rawState,...economic}:rawState;
  const [error,setError]=useState<string|null>(null);
  const [selected,setSelected]=useState<string>('');
  const [candles,setCandles]=useState<Candle[]>([]);
  const [chartError,setChartError]=useState<string|null>(null);
  const [chartAt,setChartAt]=useState<number|null>(null);
  const [candleScope,setCandleScope]=useState('');
  useEffect(()=>{
    let cancelled=false;const controller=new AbortController();
    async function load(){try{
      const r=await fetch('/api/strategy6',{cache:'no-store',signal:controller.signal});
      if(!r.ok)throw new Error('Нет связи с симулятором №6');
      const s=await r.json() as State;if(!cancelled){setState(s);setError(null);}
    }catch(e){if(!cancelled)setError(e instanceof Error?e.message:'Ошибка данных');}}
    void load();const timer=setInterval(load,15000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[]);
  const current=state?.positions.find(p=>p.id===selected)??state?.positions.find(p=>p.status==='open')??state?.positions[0];
  const symbol=current?.bybit_symbol??'BTCUSDT';const frame=current?.frame==='30m'?'30':'15';
  const positionId=current?.entered?current.id:'';
  const expectedScope=`${symbol}:${frame}:${positionId}`;
  useEffect(()=>{
    let cancelled=false;const controller=new AbortController();setCandles([]);setChartAt(null);
    async function load(){try{
      const r=await fetch(`/api/strategy6?kind=chart&symbol=${encodeURIComponent(symbol)}&frame=${frame}&position=${encodeURIComponent(positionId)}`,{cache:'no-store',signal:controller.signal});
      if(!r.ok)throw new Error('Свечи Bybit недоступны для этого контракта');
      const data=await r.json();if(!cancelled){setCandles(data.candles);setCandleScope(`${symbol}:${frame}:${positionId}`);setChartAt(data.at);setChartError(null);}
    }catch(e){if(!cancelled)setChartError(e instanceof Error?e.message:'Ошибка Bybit');}}
    void load();const timer=setInterval(load,15000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[symbol,frame,positionId,current?.closed_at]);
  const opened=state?.positions.filter(p=>p.status==='open')??[];
  const closed=(state?.positions.filter(p=>p.status==='closed')??[]).sort((a,b)=>(b.closed_at??0)-(a.closed_at??0));
  const stats=metrics(closed);
  const stale=opened.filter(p=>!p.marked_at||Date.now()-p.marked_at>1200000).length;
  return <section id="strategy6" className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
    <div className="p-4 border-b border-slate-800 flex flex-wrap justify-between gap-2">
      <div><h2 className="font-bold text-cyan-300 text-lg">Стратегия №6 v2 · {filtered?'A · E2':'A · исходная'}</h2><p className="text-xs text-slate-400">Дневной FVG → слом → откат 50–60% · 15m / 30m</p></div>
      <span className="text-xs text-cyan-200 border border-cyan-900 rounded px-2 py-1 self-start">СИМУЛЯЦИЯ · без ордеров</span>
    </div>
    <div className="p-4 space-y-4">
      <div className="rounded-lg border border-slate-700 p-3 text-sm text-slate-300 space-y-2">
        <label className="flex gap-2 items-center"><input type="checkbox" checked={filtered} onChange={e=>{setFiltered(e.target.checked);setSelected('');}}/>A · E2: прибыль до исходного тейка ≥ 2 рисков после расходов</label>
        <p className="text-xs text-slate-400">Исследовательский пересчёт сохранённых и новых модельных входов. Не новая реальная торговля. Стоп, тейк и объём прежние; комиссии 0,05% и проскальзывание 0,02% за исполнение. Будущий funding не участвует в отборе, но учитывается в результате. Исходная история сохранена.</p>
        {filtered&&economic&&<p className="text-xs">Отсеяно по правилу: {economic.skipped} · Недостаточно данных входа: {economic.unknown} · Ожидают входа: {economic.pending}. Обновление каждые 15 секунд.</p>}
      </div>
      {error&&<p role="alert" className="text-rose-300">{error}. Старые значения не являются текущими.</p>}
      {!state&&!error&&<p className="text-slate-400">Загрузка №6…</p>}
      {state&&<>
        <div className="rounded-lg border border-cyan-800 bg-cyan-950/30 p-4">
          <h3 className="text-cyan-200 font-semibold">Условный депозит · старт $10 000</h3>
          <p className="font-mono text-lg mt-2">Было {balance(10000)} → баланс {balance(10000+state.closed_net)} → с открытыми {balance(10000+state.closed_net+state.open_net)}</p>
          <p className="text-xs text-slate-400 mt-2">Закрытые: {money(state.closed_net)} ({(state.closed_net/100).toFixed(2)}% от старта). С открытыми: {money(state.closed_net+state.open_net)}. Это $10 000 + сумма независимых симуляций, не реальный баланс и не портфель с ограничением общего капитала.</p>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[['Закрытые · Σ net',money(state.closed_net)],['Открытые · модельный net',money(state.open_net)],['Прибыль / убыток',`${state.wins} / ${state.losses}`],['Открыто / закрыто',`${state.open} / ${state.closed}`]].map(([label,value])=><div key={label} className="bg-slate-950/60 rounded-lg p-3 border border-slate-800"><div className="text-[10px] text-slate-400 uppercase">{label}</div><div className="font-mono text-lg mt-1">{value}</div></div>)}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            ['Всего net · закрытые + открытые',money(state.closed_net+state.open_net)],
            ['Win rate · закрытые',stats.winRate==null?'—':`${stats.winRate.toFixed(1)}%`],
            ['Profit factor · net',stats.profitFactor==null?(stats.noLoss?'Нет убытков':'—'):stats.profitFactor.toFixed(2)],
            ['Средняя закрытая сделка',money(stats.average)],
            ['Лучший результат',money(stats.best)],['Худший результат',money(stats.worst)],
            ['Комиссии · закрытые',stats.fees==null?'—':`$${stats.fees.toFixed(2)}`],
            ['Funding · закрытые',money(stats.funding)],
            ['Просадка Σ закрытых, не счёта',`$${stats.drawdown.toFixed(2)}`],
            ['Среднее удержание',stats.hours==null?'—':`${stats.hours.toFixed(1)} ч`],
            ['Безубыточных закрытий',String(stats.flat)],['Результаты без данных',String(stats.missing)]
          ].map(([label,value])=><div key={label} className="bg-slate-950/60 rounded-lg p-3 border border-slate-800"><div className="text-[10px] text-slate-400 uppercase">{label}</div><div className="font-mono text-lg mt-1 text-cyan-100">{value}</div></div>)}
        </div>
        <h3 className="text-sm font-semibold text-cyan-200">Накопленный net закрытых сделок</h3>
        <ResultCurve points={stats.curve}/>
        <p className="text-xs text-slate-500">По времени закрытия, МСК. Это не equity счёта: внутрисделочная просадка не восстановлена. Маленькая выборка не доказывает прибыльность.</p>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{[['15m',closed.filter(p=>p.frame==='15m')],['30m',closed.filter(p=>p.frame==='30m')],['LONG',closed.filter(p=>p.sign===1)],['SHORT',closed.filter(p=>p.sign===-1)]].map(([label,rows])=>{const m=metrics(rows as Position[]);return <div key={label as string} className="rounded-lg border border-slate-800 p-3 text-xs"><b>{label as string}</b><p className="font-mono text-base my-1">{money(m.total)}</p><p>{m.count} закрыто · {m.wins} плюс / {m.losses} минус</p></div>})}</div>
        <p className="text-xs text-slate-400">Пропущено {state.skipped} · ожидают {state.pending}. Условная база $10 000, плановый риск до $100 на независимый сетап. Σ net — сумма отдельных расчётов, не доходность общего счёта.</p>
        <p className="text-xs text-amber-200/80">Учёт сделок: OKX / SIM-001. Котировки и график: Bybit USDT perpetual. Цены бирж могут различаться; котировки Bybit не меняют историю PnL.</p>
        {(state.quote_error||stale>0)&&<p className="text-xs text-rose-300">{state.quote_error} {stale>0?`Устаревших оценок открытых позиций: ${stale}`:''}</p>}
        <div className="flex flex-wrap gap-2 items-center justify-between"><label className="text-xs text-slate-400">График сделки <select aria-label="Сделка №6" value={current?.id??''} onChange={e=>setSelected(e.target.value)} className="ml-2 bg-slate-950 border border-slate-700 rounded p-2 text-white max-w-full">{state.positions.map(p=><option key={p.id} value={p.id}>{p.asset} · {p.frame} · {p.sign===1?'LONG':'SHORT'} · {stamp(p.entered)} · {p.status}</option>)}</select></label><span className="text-xs text-slate-500">Bybit {stamp(chartAt)} МСК</span></div>
        {chartError?<p className="text-amber-300 text-sm">{chartError}</p>:<N6InteractiveChart key={current?.id??'empty'} candles={candleScope===expectedScope?candles:[]} position={current}/>}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">{current?.fills?.map((f,i)=><div key={`${f.kind}-${f.at}-${i}`} className={`rounded border p-3 ${f.kind==='entry'?'border-cyan-800 text-cyan-200':'border-amber-800 text-amber-200'}`}><b>{reason(f.kind)}</b><p className="font-mono">{price(f.price)} · {stamp(f.at)} МСК</p><p className="text-xs text-slate-400">{f.kind==='entry'?'Цена исполнения модели со проскальзыванием':'Цена исполнения модели; время фиксации по завершению минутной свечи'}</p></div>)}</div>
        {current?.status==='open'&&<p className="text-cyan-200 text-sm">Позиция открыта — выхода ещё нет.</p>}
        {current?.status==='closed'&&!current.fills?.some(f=>f.kind!=='entry')&&<p className="text-amber-300">Событие выхода недоступно; цену выхода не восстанавливаем из стопа.</p>}
        <p className="text-[11px] text-slate-500">Линии входа / стопа / цели — уровни симулятора OKX поверх графика Bybit. Последняя свеча может быть незакрытой.</p>
        <h3 className="text-sm font-semibold">Открытые позиции</h3><Trades positions={opened} onSelect={setSelected}/>
        <h3 className="text-sm font-semibold">Журнал закрытых · {closed.length}</h3><div className="max-h-96 overflow-auto"><Trades positions={closed} onSelect={setSelected}/></div>
        <p className="text-[11px] text-slate-500">Обновление панели каждые 15 секунд · снимок {stamp(state.at)} МСК · комиссия 0,05% и проскальзывание 0,02% за исполнение · funding предварительный.</p>
      </>}
    </div>
  </section>;
}

function ResultCurve({points}:{points:{time:number;value:number}[]}){
  if(points.length<2)return <p className="text-slate-500 text-xs">График появится после первого закрытия.</p>;
  const lo=Math.min(0,...points.map(p=>p.value)),hi=Math.max(0,...points.map(p=>p.value)),range=hi-lo||1;
  const y=(v:number)=>165-(v-lo)/range*130;
  const start=points[0].time,end=points[points.length-1].time;
  const x=(t:number)=>65+(t-start)/Math.max(1,end-start)*760;
  return <svg viewBox="0 0 940 205" className="w-full rounded-lg bg-slate-950 border border-slate-800" role="img" aria-label="Накопленный net закрытых сделок стратегии №6">
    <line x1="65" x2="830" y1={y(0)} y2={y(0)} stroke="#475569" strokeDasharray="4 4"/>
    <polyline points={points.map(p=>`${x(p.time)},${y(p.value)}`).join(' ')} fill="none" stroke="#22d3ee" strokeWidth="2"/>
    <text x="5" y="25" fill="#94a3b8" fontSize="11">{money(hi)}</text><text x="5" y="175" fill="#94a3b8" fontSize="11">{money(lo)}</text>
    <text x="65" y="195" fill="#94a3b8" fontSize="11">{stamp(start)}</text><text x="680" y="195" fill="#94a3b8" fontSize="11">{stamp(end)}</text>
    <text x="835" y={y(points[points.length-1].value)+4} fill="#22d3ee" fontSize="11">{money(points[points.length-1].value)}</text>
  </svg>;
}

function Trades({positions,onSelect}:{positions:Position[];onSelect:(id:string)=>void}){
  if(!positions.length)return <p className="text-xs text-slate-500">Пока нет сделок.</p>;
  return <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap"><thead className="text-slate-500"><tr>{['Актив / TF','Вход','Стоп','Цель','Bybit сейчас','Net модели','Время МСК'].map(x=><th className="text-left p-2" key={x}>{x}</th>)}</tr></thead><tbody>{positions.map(p=><tr key={p.id} className="border-t border-slate-800"><td className="p-2"><button className={p.sign===1?'text-emerald-400':'text-rose-400'} onClick={()=>onSelect(p.id)}>{p.sign===1?'▲':'▼'} {p.asset} · {p.frame}</button></td><td className="p-2 font-mono">{price(p.avg)}</td><td className="p-2 font-mono">{price(p.stop)}</td><td className="p-2 font-mono">{price(p.t2)}</td><td className="p-2 font-mono">{price(p.bybit_last)}</td><td className={`p-2 font-mono ${(p.net??0)>=0?'text-emerald-400':'text-rose-400'}`}>{money(p.net)}</td><td className="p-2 text-slate-400">{stamp(p.closed_at??p.entered)}</td></tr>)}</tbody></table></div>;
}

function Chart({candles,position}:{candles:Candle[];position:Position|undefined}){
  if(!candles.length)return <div className="h-48 flex items-center justify-center text-slate-500 text-sm">Загрузка свечей Bybit…</div>;
  const levels=[{name:'Вход',value:position?.avg,color:'#38bdf8'},{name:'Стоп',value:position?.stop,color:'#fb7185'},{name:'Цель',value:position?.t2,color:'#34d399'}].filter(l=>l.value!=null&&l.value>0);
  const fills=(position?.fills??[]).filter(f=>Number.isFinite(f.price)&&Number.isFinite(f.at));
  const values=[...candles.flatMap(c=>[c.low,c.high]),...levels.map(l=>l.value as number),...fills.map(f=>f.price)];
  const lo=Math.min(...values),hi=Math.max(...values),pad=(hi-lo)*.08||1;
  const y=(v:number)=>250-(v-lo+pad)/(hi-lo+2*pad)*225;
  const x=(i:number)=>15+i*780/Math.max(1,candles.length-1);
  const first=candles[0].time,step=(position?.frame==='30m'?30:15)*60000;
  const fx=(at:number)=>15+(at-first)/step*780/Math.max(1,candles.length-1);
  const labels=levels.map(l=>({...l,labelY:y(l.value as number)})).sort((a,b)=>a.labelY-b.labelY);
  for(let i=1;i<labels.length;i++)labels[i].labelY=Math.max(labels[i].labelY,labels[i-1].labelY+18);
  return <svg viewBox="0 0 1020 320" role="img" aria-label="Свечи Bybit, точки входа и выхода симулятора №6" className="w-full rounded-lg bg-slate-950 border border-slate-800">
    {candles.map((c,i)=><g key={c.time} stroke={c.close>=c.open?'#34d399':'#fb7185'} fill={c.close>=c.open?'#34d399':'#fb7185'}><line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)}/><rect x={x(i)-2} y={Math.min(y(c.open),y(c.close))} width="4" height={Math.max(1,Math.abs(y(c.open)-y(c.close)))}/></g>)}
    {labels.map(l=><g key={l.name}><line x1="8" x2="805" y1={y(l.value as number)} y2={y(l.value as number)} stroke={l.color} strokeDasharray="5 4"/><line x1="805" x2="817" y1={y(l.value as number)} y2={l.labelY} stroke={l.color}/><text x="820" y={l.labelY+4} fill={l.color} fontSize="12">{l.name} {price(l.value as number)}</text></g>)}
    {fills.filter(f=>f.at>=first&&f.at<candles[candles.length-1].time+step).map((f,i)=>{const xx=fx(f.at),yy=y(f.price),entry=f.kind==='entry',color=entry?'#22d3ee':'#fbbf24';return <g key={`${f.kind}-${i}`}><title>{reason(f.kind)} {price(f.price)} · {stamp(f.at)} МСК</title><line x1={xx} x2={xx} y1="12" y2="250" stroke={color} strokeDasharray="2 5" opacity="0.6"/><circle cx={xx} cy={yy} r="6" stroke={color} strokeWidth="2" fill="#020617"/><path d={`M ${xx-5} ${yy+(entry?15:-15)} L ${xx} ${yy+(entry?8:-8)} L ${xx+5} ${yy+(entry?15:-15)}`} fill="none" stroke={color} strokeWidth="2"/><text x={Math.max(20,Math.min(680,xx))} y={entry?15:303} fill={color} fontSize="12">{reason(f.kind)} {price(f.price)}</text></g>})}
    <text x="15" y="275" fill="#94a3b8" fontSize="11">{stamp(candles[0].time)} МСК</text><text x="650" y="275" fill="#94a3b8" fontSize="11">{stamp(candles[candles.length-1].time)} МСК</text>
  </svg>;
}
