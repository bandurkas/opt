"use client";
import { useEffect, useState } from "react";
import FvgDcaChart, {type FvgChartCandle} from "./FvgDcaChart";

type Event = {kind:string;at:number;price?:number;mark_price?:number;qty?:number;fee?:number;net?:number;reason?:string};
type Position = {id:string;asset:string;symbol:string;frame:"1D"|"1W";side:"bullish"|"bearish";
  status:string;sent_at:number;entered?:number;closed_at?:number;fills:number;avg:number;q:number;
  margin:number;fees:number;funding:number;net:number;last?:number;marked_at?:number;
  level?:string;fvg_low?:string;fvg_high?:string;entry_rule?:string;entry_low?:string;entry_high?:string;
  liquidation_est?:number;target_est?:number;target_net:number;ambiguous?:boolean;
  data_error?:string;events:Event[]};
type Summary = {signals:number;closed:number;open:number;skipped:number;wins:number;losses:number;
  liquidations:number;closed_gross:number;closed_net:number;open_net:number;fees:number;funding:number;
  open_margin:number;open_notional:number;data_errors:number};
type State = {entry_condition?:string;at:number;mode:string;source:string;leverage:number;margin_per_entry:number;
  total:Summary;daily:Summary;weekly:Summary;max_closed_drawdown:number;
  worst_closed:number|null;positions:Position[]};
type ChartState={at:number;source:string;symbol:string;interval:"1H"|"4H"|"1D"|"1W";
  candles:FvgChartCandle[];entry_visible:boolean;exit_visible:boolean;clipped:boolean};

const usd=(n:number|null|undefined)=>n==null?"—":`${n<0?"−":"+"}$${Math.abs(n).toFixed(2)}`;
const amount=(n:number|null|undefined)=>n==null?"—":`$${n.toFixed(2)}`;
const price=(n:number|null|undefined)=>n==null?"—":n.toLocaleString("ru-RU",{maximumSignificantDigits:8});
const msk=(n:number|null|undefined)=>n==null?"—":new Date(n).toLocaleString("ru-RU",{
  timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});

function SummaryCard({title,data}:{title:string;data:Summary}){
  return <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3 space-y-2">
    <h4 className="font-semibold text-slate-100">{title}</h4>
    <div className="text-sm text-slate-300">Сигналы {data.signals} · закрыто {data.closed} · открыто {data.open} · пропущено {data.skipped}</div>
    <div className="text-sm text-slate-300">Плюс {data.wins} / минус {data.losses} · ликвидаций {data.liquidations}</div>
    <div className={`font-mono ${data.closed_net>=0?"text-emerald-300":"text-rose-300"}`}>Закрытые net: {usd(data.closed_net)}</div>
    <div className="text-xs text-slate-400">Закрытый gross {usd(data.closed_gross)} · открытые net {usd(data.open_net)}</div>
    <div className="text-xs text-slate-400">Комиссии {amount(data.fees)} · funding {usd(data.funding)} (по всем позициям)</div>
    <div className="text-xs text-slate-400">Сейчас занято маржи {amount(data.open_margin)} · номинал {amount(data.open_notional)}</div>
    {data.data_errors>0&&<div className="text-xs text-amber-300">Проблемы с данными: {data.data_errors}; текущий PnL может устареть.</div>}
  </div>;
}

export default function FvgDcaPanel(){
  const [state,setState]=useState<State|null>(null);
  const [error,setError]=useState<string|null>(null);
  const [selected,setSelected]=useState("");
  const [interval,setIntervalValue]=useState<"1H"|"4H"|"1D"|"1W">("1H");
  const [frameFilter,setFrameFilter]=useState("all");
  const [statusFilter,setStatusFilter]=useState("all");
  const [chart,setChart]=useState<ChartState|null>(null);
  const [chartError,setChartError]=useState<string|null>(null);
  useEffect(()=>{
    let cancelled=false;const controller=new AbortController();
    async function load(){try{
      const response=await fetch("/api/fvg-dca",{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error("Нет связи с FVG-симулятором");
      const data=await response.json() as State;
      if(!cancelled){setState(data);setError(null);}
    }catch(e){if(!cancelled)setError(e instanceof Error?e.message:"Ошибка данных");}}
    void load();const timer=setInterval(load,60_000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[]);
  const current=state?.positions.find(p=>p.id===selected)??
    state?.positions.find(p=>p.status==="open")??state?.positions[0];
  useEffect(()=>{
    if(!current)return;
    let cancelled=false;const controller=new AbortController();
    setChart(null);setChartError(null);
    async function load(){try{
      const url=`/api/fvg-dca?kind=chart&position=${encodeURIComponent(current!.id)}&interval=${interval}`;
      const response=await fetch(url,{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error("Свечи OKX недоступны для выбранной сделки");
      const data=await response.json() as ChartState;
      if(!cancelled){setChart(data);setChartError(null);}
    }catch(e){if(!cancelled)setChartError(e instanceof Error?e.message:"Ошибка графика");}}
    void load();const timer=setInterval(load,60_000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[current?.id,interval]);
  const visible=state?.positions.filter(p=>(frameFilter==="all"||p.frame===frameFilter)&&
    (statusFilter==="all"||p.status===statusFilter))??[];
  return <section id="fvg-dca" className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
    <div className="p-4 border-b border-slate-800 flex flex-wrap justify-between gap-3">
      <div><h2 className="font-bold text-cyan-200 text-lg">FVG A · дневные и недельные</h2>
        <p className="text-xs text-slate-400">Зона A…A + 1,5% · два входа по $10 · 10× · интерактивный график OKX</p></div>
      <span className="text-xs text-amber-200 border border-amber-900 rounded px-2 py-1 self-start">СИМУЛЯЦИЯ · без ордеров</span>
    </div>
    <div className="p-4 space-y-4">
      <p className="text-sm text-slate-300">Первый вход: $10 маржи после Telegram, на следующей минуте, только пока цена в зоне. Старые сделки сохранены по прежним правилам. При −$5 по первой части добавление ещё $10.
        Цель: +$10 net без добавления или +$20 net после него. Стопа нет; ликвидация расчётная.
        Комиссия 0,05% и проскальзывание 0,02% на исполнение; funding по доступной истории OKX.</p>
      <p className="text-xs text-amber-300">Ликвидация — оценка по mark price и публичному tier; она может отличаться от фактической на личном аккаунте.
        Открытые позиции и пропуски не включены в прибыль закрытых. Доходность не доказана.</p>
      {error&&<p role="alert" className="text-rose-300">{error}. Старые цифры не выдаю за текущие.</p>}
      {!state&&!error&&<p className="text-slate-400">Загрузка FVG…</p>}
      {state&&<>
        <p className="text-sm text-cyan-200">Условие новых входов: {state.entry_condition ?? "загрузка правила"}. Цена исполнения учитывает проскальзывание.</p>
        <div className="grid md:grid-cols-2 gap-3">
          <SummaryCard title="Дневные FVG · 1D" data={state.daily}/>
          <SummaryCard title="Недельные FVG · 1W" data={state.weekly}/>
        </div>
        <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-300">
          Вместе: закрытые <span className="font-mono">{usd(state.total.closed_net)}</span> ·
          оценка открытых <span className="font-mono">{usd(state.total.open_net)}</span> ·
          максимум просадки закрытой кривой {amount(state.max_closed_drawdown)} ·
          худшая закрытая {usd(state.worst_closed)}. Это не единый торговый счёт.
        </div>
        {state.positions.length===0&&<p className="text-slate-400 text-sm">Новых уведомлений после запуска симулятора пока не было. История и график сделок появятся после первого нового касания; старые сигналы не превращаются в виртуальные сделки.</p>}
        {current&&<div className="space-y-3 rounded-lg border border-slate-700 bg-slate-950/50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="font-semibold text-slate-100">График · {current.asset} · {current.frame} · {current.side==="bullish"?"🟢 ЛОНГ":"🔴 ШОРТ"}</h3>
              <p className="text-xs text-slate-400">Сигнал {msk(current.sent_at)} МСК · входы {current.fills} · {current.status}</p></div>
            <div className="flex gap-1">{(["1H","4H","1D","1W"] as const).map(x=><button key={x}
              onClick={()=>setIntervalValue(x)} className={`rounded px-3 py-2 text-xs ${interval===x?"bg-cyan-800 text-white":"bg-slate-800 text-slate-300"}`}>{x}</button>)}</div>
          </div>
          <p className="text-xs text-amber-200">{current.entry_rule==="A_UP_1_5"?
            `Диапазон этого сигнала: ${price(Number(current.entry_low))}–${price(Number(current.entry_high))} (A…A + 1,5%)`:
            "Ранее открытая позиция: прежнее правило точного касания A"}</p>
          {chartError&&<p role="alert" className="text-rose-300 text-sm">{chartError}</p>}
          {!chart&&!chartError&&<p className="text-slate-400 text-sm">Загрузка свечей OKX…</p>}
          {chart&&<>
            {(!chart.entry_visible||current.closed_at&&!chart.exit_visible||chart.clipped)&&
              <p className="text-amber-300 text-xs">Не все метки помещаются в окно {interval}. Выберите 4H, 1D или 1W; результаты сделки от масштаба не меняются.</p>}
            <FvgDcaChart key={`${current.id}:${interval}`} candles={chart.candles} position={current} interval={interval}/>
            <p className="text-xs text-slate-500">Свечи: OKX · обновлено {msk(chart.at)} МСК · <a className="text-cyan-300 hover:underline" href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${current.asset}USDT.P`)}&interval=${interval}`} target="_blank" rel="noopener noreferrer">🔗 Открыть в TradingView</a></p>
          </>}
          <div className="flex flex-wrap gap-3 text-xs text-slate-300">{current.events.map((e,i)=><span key={i} className="rounded bg-slate-800 px-2 py-1">
            {e.kind==="entry"?"Вход 1":e.kind==="add"?"Вход 2":e.kind==="target"?"Тейк":e.kind==="liquidation_estimate"?"Расчётная ликвидация":e.kind} · {msk(e.at)} · {price(e.price??e.mark_price)}
          </span>)}</div>
        </div>}
        {state.positions.length>0&&<div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-100 mr-auto">История сигналов и сделок · {visible.length} из {state.positions.length}</h3>
            <select aria-label="Фильтр таймфрейма" className="bg-slate-800 rounded p-2 text-xs" value={frameFilter} onChange={e=>setFrameFilter(e.target.value)}><option value="all">1D + 1W</option><option value="1D">1D</option><option value="1W">1W</option></select>
            <select aria-label="Фильтр статуса" className="bg-slate-800 rounded p-2 text-xs" value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}><option value="all">Все статусы</option><option value="open">Открытые</option><option value="closed">Тейк</option><option value="liquidated">Ликвидация</option><option value="skipped">Пропуск</option></select>
          </div>
          <div className="max-h-[520px] overflow-auto">
          <table className="w-full text-xs text-left text-slate-300"><thead><tr className="border-b border-slate-700 text-slate-400">
            <th className="p-2">Актив / зона</th><th className="p-2">Статус</th><th className="p-2">Входы</th>
            <th className="p-2">Средняя / цель / ликв.</th><th className="p-2">Net</th><th className="p-2">Время</th>
          </tr></thead><tbody>{visible.map(p=>{
            const href=`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${p.asset}USDT.P`)}&interval=${p.frame}`;
            const direction=p.side==="bullish"?"🟢 LONG":"🔴 SHORT";
            const details=p.events.filter(e=>e.kind==="entry"||e.kind==="add");
            return <tr key={p.id} className={`border-b border-slate-800 align-top ${current?.id===p.id?"bg-cyan-950/30":""}`}>
              <td className="p-2 whitespace-nowrap"><button onClick={()=>setSelected(p.id)} className="text-cyan-200 hover:underline font-semibold">{p.asset} · {p.frame}</button><br/>{direction}<br/>
                <span className="text-slate-500">{p.entry_rule==="A_UP_1_5"?"A…A + 1,5%":"Прежнее касание A"}</span><br/>
                <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan-300 hover:underline">🔗 График</a></td>
              <td className="p-2">{p.status==="liquidated"?"Ликвидация (оценка)":p.status==="closed"?"Цель":p.status==="open"?"Открыта":p.status==="pending"?"Ожидает входа":"Пропущена"}
                {p.data_error&&<div className="text-amber-300">Данные устарели</div>}
                {p.status==="skipped"&&p.events.some(e=>e.reason?.startsWith("Price left"))&&<div className="text-slate-400">Цена вышла из диапазона до входа</div>}
                {p.ambiguous&&<div className="text-amber-300">Неоднозначная минута</div>}</td>
              <td className="p-2 whitespace-nowrap">{details.length?details.map((e,i)=><div key={i}>{i+1}: {price(e.price)} · {msk(e.at)}</div>):"—"}</td>
              <td className="p-2 whitespace-nowrap">{price(p.avg)}<br/>цель {price(p.target_est)}<br/>ликв. {price(p.liquidation_est)}</td>
              <td className={`p-2 font-mono ${p.net>=0?"text-emerald-300":"text-rose-300"}`}>{p.status==="skipped"?"—":usd(p.net)}<br/>
                <span className="text-slate-500">ком. {amount(p.fees)} · фонд. {usd(p.funding)}</span></td>
              <td className="p-2 whitespace-nowrap">сигнал {msk(p.sent_at)}<br/>вход {msk(p.entered)}<br/>выход {msk(p.closed_at)}</td>
            </tr>;
          })}</tbody></table>
          </div>
        </div>}
        <p className="text-xs text-slate-500">Обновлено {msk(state.at)} МСК. Одна позиция на актив; пропуски и ошибки данных видны в таблице.</p>
      </>}
    </div>
  </section>;
}
