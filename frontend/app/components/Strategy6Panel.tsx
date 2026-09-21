"use client";
import { useEffect, useState } from "react";

type Position = { id:string; asset:string; bybit_symbol:string; frame:string; status:string; sign:number;
  avg:number|null; stop:number|null; t2:number|null; net:number|null; marked_at:number|null;
  entered:number|null; closed_at:number|null; bybit_last:number|null };
type State = { at:number; quote_at:number|null; quote_error:string|null; closed:number; open:number;
  wins:number; losses:number; skipped:number; pending:number; closed_net:number; open_net:number; positions:Position[] };
type Candle = {time:number;open:number;high:number;low:number;close:number};
const money=(v:number|null)=>v==null?'—':`${v>=0?'+':'−'}$${Math.abs(v).toFixed(2)}`;
const price=(v:number|null)=>v==null?'—':v.toLocaleString('ru-RU',{maximumSignificantDigits:7});
const stamp=(v:number|null)=>v==null?'—':new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});

export default function Strategy6Panel(){
  const [state,setState]=useState<State|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [selected,setSelected]=useState<string>('');
  const [candles,setCandles]=useState<Candle[]>([]);
  const [chartError,setChartError]=useState<string|null>(null);
  const [chartAt,setChartAt]=useState<number|null>(null);
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
  useEffect(()=>{
    let cancelled=false;const controller=new AbortController();setCandles([]);setChartAt(null);
    async function load(){try{
      const r=await fetch(`/api/strategy6?kind=chart&symbol=${encodeURIComponent(symbol)}&frame=${frame}`,{cache:'no-store',signal:controller.signal});
      if(!r.ok)throw new Error('Свечи Bybit недоступны для этого контракта');
      const data=await r.json();if(!cancelled){setCandles(data.candles);setChartAt(data.at);setChartError(null);}
    }catch(e){if(!cancelled)setChartError(e instanceof Error?e.message:'Ошибка Bybit');}}
    void load();const timer=setInterval(load,15000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[symbol,frame]);
  const opened=state?.positions.filter(p=>p.status==='open')??[];
  const closed=state?.positions.filter(p=>p.status==='closed')??[];
  const stale=opened.filter(p=>!p.marked_at||Date.now()-p.marked_at>1200000).length;
  return <section id="strategy6" className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
    <div className="p-4 border-b border-slate-800 flex flex-wrap justify-between gap-2">
      <div><h2 className="font-bold text-cyan-300 text-lg">Стратегия №6 v2 · A</h2><p className="text-xs text-slate-400">Дневной FVG → слом → откат 50–60% · 15m / 30m</p></div>
      <span className="text-xs text-cyan-200 border border-cyan-900 rounded px-2 py-1 self-start">СИМУЛЯЦИЯ · без ордеров</span>
    </div>
    <div className="p-4 space-y-4">
      {error&&<p role="alert" className="text-rose-300">{error}. Старые значения не являются текущими.</p>}
      {!state&&!error&&<p className="text-slate-400">Загрузка №6…</p>}
      {state&&<>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[['Закрытые · Σ net',money(state.closed_net)],['Открытые · модельный net',money(state.open_net)],['Прибыль / убыток',`${state.wins} / ${state.losses}`],['Открыто / закрыто',`${state.open} / ${state.closed}`]].map(([label,value])=><div key={label} className="bg-slate-950/60 rounded-lg p-3 border border-slate-800"><div className="text-[10px] text-slate-400 uppercase">{label}</div><div className="font-mono text-lg mt-1">{value}</div></div>)}
        </div>
        <p className="text-xs text-slate-400">Пропущено {state.skipped} · ожидают {state.pending}. Условная база $10 000, плановый риск до $100 на независимый сетап. Σ net — сумма отдельных расчётов, не доходность общего счёта.</p>
        <p className="text-xs text-amber-200/80">Учёт сделок: OKX / SIM-001. Котировки и график: Bybit USDT perpetual. Цены бирж могут различаться; котировки Bybit не меняют историю PnL.</p>
        {(state.quote_error||stale>0)&&<p className="text-xs text-rose-300">{state.quote_error} {stale>0?`Устаревших оценок открытых позиций: ${stale}`:''}</p>}
        <div className="flex flex-wrap gap-2 items-center justify-between"><label className="text-xs text-slate-400">График сделки <select aria-label="Сделка №6" value={current?.id??''} onChange={e=>setSelected(e.target.value)} className="ml-2 bg-slate-950 border border-slate-700 rounded p-2 text-white max-w-full">{state.positions.map(p=><option key={p.id} value={p.id}>{p.asset} · {p.frame} · {p.sign===1?'LONG':'SHORT'} · {stamp(p.entered)} · {p.status}</option>)}</select></label><span className="text-xs text-slate-500">Bybit {stamp(chartAt)} МСК</span></div>
        {chartError?<p className="text-amber-300 text-sm">{chartError}</p>:<Chart candles={candles} position={current}/>}
        <p className="text-[11px] text-slate-500">Линии входа / стопа / цели — уровни симулятора OKX поверх графика Bybit. Последняя свеча может быть незакрытой.</p>
        <h3 className="text-sm font-semibold">Открытые позиции</h3><Trades positions={opened} onSelect={setSelected}/>
        <h3 className="text-sm font-semibold">Последние закрытые · {closed.length}</h3><Trades positions={closed.slice(0,50)} onSelect={setSelected}/>
        <p className="text-[11px] text-slate-500">Обновление панели каждые 15 секунд · снимок {stamp(state.at)} МСК · комиссия 0,05% и проскальзывание 0,02% за исполнение · funding предварительный.</p>
      </>}
    </div>
  </section>;
}

function Trades({positions,onSelect}:{positions:Position[];onSelect:(id:string)=>void}){
  if(!positions.length)return <p className="text-xs text-slate-500">Пока нет сделок.</p>;
  return <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap"><thead className="text-slate-500"><tr>{['Актив / TF','Вход','Стоп','Цель','Bybit сейчас','Net модели','Время МСК'].map(x=><th className="text-left p-2" key={x}>{x}</th>)}</tr></thead><tbody>{positions.map(p=><tr key={p.id} className="border-t border-slate-800"><td className="p-2"><button className={p.sign===1?'text-emerald-400':'text-rose-400'} onClick={()=>onSelect(p.id)}>{p.sign===1?'▲':'▼'} {p.asset} · {p.frame}</button></td><td className="p-2 font-mono">{price(p.avg)}</td><td className="p-2 font-mono">{price(p.stop)}</td><td className="p-2 font-mono">{price(p.t2)}</td><td className="p-2 font-mono">{price(p.bybit_last)}</td><td className={`p-2 font-mono ${(p.net??0)>=0?'text-emerald-400':'text-rose-400'}`}>{money(p.net)}</td><td className="p-2 text-slate-400">{stamp(p.closed_at??p.entered)}</td></tr>)}</tbody></table></div>;
}

function Chart({candles,position}:{candles:Candle[];position:Position|undefined}){
  if(!candles.length)return <div className="h-48 flex items-center justify-center text-slate-500 text-sm">Загрузка свечей Bybit…</div>;
  const levels=[{name:'Вход',value:position?.avg,color:'#38bdf8'},{name:'Стоп',value:position?.stop,color:'#fb7185'},{name:'Цель',value:position?.t2,color:'#34d399'}].filter(l=>l.value!=null&&l.value>0);
  const values=[...candles.flatMap(c=>[c.low,c.high]),...levels.map(l=>l.value as number)];
  const lo=Math.min(...values),hi=Math.max(...values),pad=(hi-lo)*.08||1;
  const y=(v:number)=>250-(v-lo+pad)/(hi-lo+2*pad)*225;
  const x=(i:number)=>15+i*780/Math.max(1,candles.length-1);
  return <svg viewBox="0 0 940 285" role="img" aria-label="Свечи Bybit и уровни симулятора №6" className="w-full rounded-lg bg-slate-950 border border-slate-800">
    {candles.map((c,i)=><g key={c.time} stroke={c.close>=c.open?'#34d399':'#fb7185'} fill={c.close>=c.open?'#34d399':'#fb7185'}><line x1={x(i)} x2={x(i)} y1={y(c.high)} y2={y(c.low)}/><rect x={x(i)-2} y={Math.min(y(c.open),y(c.close))} width="4" height={Math.max(1,Math.abs(y(c.open)-y(c.close)))}/></g>)}
    {levels.map(l=><g key={l.name}><line x1="8" x2="805" y1={y(l.value as number)} y2={y(l.value as number)} stroke={l.color} strokeDasharray="5 4"/><text x="812" y={y(l.value as number)+4} fill={l.color} fontSize="11">{l.name} {price(l.value as number)}</text></g>)}
    <text x="15" y="275" fill="#94a3b8" fontSize="11">{stamp(candles[0].time)} МСК</text><text x="650" y="275" fill="#94a3b8" fontSize="11">{stamp(candles[candles.length-1].time)} МСК</text>
  </svg>;
}
