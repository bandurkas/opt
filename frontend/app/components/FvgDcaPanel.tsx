"use client";
import { useEffect, useState } from "react";
import FvgDcaChart, {type FvgChartCandle} from "./FvgDcaChart";
import {closedEconomics,closeReason,historyPositions,visiblePositions} from "./fvgDcaView";

type Event = {kind:string;at:number;price?:number;mark_price?:number;qty?:number;fee?:number;net?:number;reason?:string};
type Position = {id:string;asset:string;symbol:string;frame:"1H"|"1D"|"1W";side:"bullish"|"bearish";
  status:string;sent_at:number;entered?:number;closed_at?:number;fills:number;avg:number;q:number;
  margin:number;fees:number;funding:number;net:number;last?:number;marked_at?:number;
  level?:string;origin_ts?:number;fvg_low?:string;fvg_high?:string;entry_rule?:string;entry_low?:string;entry_high?:string;
  liquidation_est?:number;target_est?:number;stop_est?:number;target_net:number;ambiguous?:boolean;exit_reason?:string;
  data_error?:string;events:Event[]};
type Summary = {signals:number;closed:number;open:number;skipped:number;wins:number;losses:number;
  liquidations:number;closed_gross:number;closed_net:number;open_net:number;fees:number;funding:number;
  open_margin:number;open_notional:number;data_errors:number};
type State = {entry_condition?:string;at:number;mode:string;source:string;leverage:number;margin_per_entry:number;
  total:Summary;daily:Summary;weekly:Summary;max_closed_drawdown:number;
  worst_closed:number|null;positions:Position[];capital?:{initial:number;balance:number;reserved:number;available:number;open_paid_costs:number;estimated_equity:number};
  activated_at?:number;last_scan?:{at:number;checked:number;universe:number;excluded:string[];errors:Record<string,string>}|null;
  zones?:Record<string,number>;
  alternative?:State|null;alternative_error?:string;comparison?:{matched_entries:number;base_closed:number;base_closed_net:number;both_closed:number}};
type ChartState={at:number;source:string;symbol:string;interval:"1H"|"4H"|"1D"|"1W";
  candles:FvgChartCandle[];entry_visible:boolean;exit_visible:boolean;origin_visible:boolean;clipped:boolean};

const usd=(n:number|null|undefined)=>n==null?"—":`${n<0?"−":"+"}$${Math.abs(n).toFixed(2)}`;
const amount=(n:number|null|undefined)=>n==null?"—":`$${n.toFixed(2)}`;
const price=(n:number|null|undefined)=>n==null?"—":n.toLocaleString("ru-RU",{maximumSignificantDigits:8});
const msk=(n:number|null|undefined)=>n==null?"—":new Date(n).toLocaleString("ru-RU",{
  timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});

function SummaryCard({title,data,positions}:{title:string;data:Summary;positions:Position[]}){
  const economics=closedEconomics(positions);
  return <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3 space-y-2">
    <h4 className="font-semibold text-slate-100">{title}</h4>
    <div className="text-sm text-slate-300">Сделки {data.signals-data.skipped} · закрыто {data.closed} · открыто {data.open}</div>
    <div className="text-sm text-slate-300">Плюс {data.wins} / минус {data.losses} · ликвидаций {data.liquidations}</div>
    <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-sm">
      <span className="text-emerald-300">Заработано: {amount(economics.earned)}</span>
      <span className="text-rose-300">Потеряно: {amount(economics.lost)}</span>
    </div>
    {economics.missing>0&&<p className="text-xs text-amber-300">Не хватает net у {economics.missing} закрытых сделок; суммы неполные.</p>}
    <div className={`font-mono ${data.closed_net>=0?"text-emerald-300":"text-rose-300"}`}>Закрытые net: {usd(data.closed_net)}</div>
    <div className="text-xs text-slate-400">Закрытый gross {usd(data.closed_gross)} · открытые net {usd(data.open_net)}</div>
    <div className="text-xs text-slate-400">Комиссии {amount(data.fees)} · funding {usd(data.funding)} (по всем позициям)</div>
    <div className="text-xs text-slate-400">Сейчас занято маржи {amount(data.open_margin)} · номинал {amount(data.open_notional)}</div>
    {data.data_errors>0&&<div className="text-xs text-amber-300">Проблемы с данными: {data.data_errors}; текущий PnL может устареть.</div>}
  </div>;
}

export default function FvgDcaPanel({hourly=false}:{hourly?:boolean}){
  const api=hourly?"/api/fvg-hourly":"/api/fvg-dca";
  const [baseState,setState]=useState<State|null>(null);
  const [scenario,setScenario]=useState<"base"|"alt">("base");
  const alternative=hourly||scenario==="alt";
  const state=!hourly&&alternative?baseState?.alternative??null:baseState;
  const [error,setError]=useState<string|null>(null);
  const [selected,setSelected]=useState("");
  const [interval,setIntervalValue]=useState<"1H"|"4H"|"1D"|"1W">("1H");
  const [frameFilter,setFrameFilter]=useState("all");
  const [statusFilter,setStatusFilter]=useState("all");
  const [historyOrder,setHistoryOrder]=useState("open_first");
  const [chart,setChart]=useState<ChartState|null>(null);
  const [chartError,setChartError]=useState<string|null>(null);
  useEffect(()=>{
    let cancelled=false;const controller=new AbortController();
    async function load(){try{
      const response=await fetch(api,{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error("Нет связи с FVG-симулятором");
      const data=await response.json() as State;
      if(!cancelled){setState(data);setError(null);}
    }catch(e){if(!cancelled)setError(e instanceof Error?e.message:"Ошибка данных");}}
    void load();const timer=setInterval(load,60_000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[api]);
  const positions=visiblePositions(state?.positions??[]);
  const current=positions.find(p=>p.id===selected)??
    positions.find(p=>p.status==="open")??positions[0];
  useEffect(()=>{
    if(!current)return;
    let cancelled=false;const controller=new AbortController();
    setChart(null);setChartError(null);
    async function load(){try{
      const url=`${api}?kind=chart&position=${encodeURIComponent(current!.id)}&interval=${interval}`;
      const response=await fetch(url,{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error("Свечи OKX недоступны для выбранной сделки");
      const data=await response.json() as ChartState;
      if(!cancelled){setChart(data);setChartError(null);}
    }catch(e){if(!cancelled)setChartError(e instanceof Error?e.message:"Ошибка графика");}}
    void load();const timer=setInterval(load,60_000);
    return()=>{cancelled=true;controller.abort();clearInterval(timer);};
  },[api,current?.id,interval]);
  const visible=historyPositions(positions,frameFilter,statusFilter,historyOrder);
  return <section id="fvg-dca" className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
    <div className="p-4 border-b border-slate-800 flex flex-wrap justify-between gap-3">
      <div><h2 className="font-bold text-cyan-200 text-lg">{hourly?"FVG A · часовая альтернатива · 1H":"FVG A · дневные и недельные"}</h2>
        <p className="text-xs text-slate-400">Касание A · бюджет $1 000 · два входа по $10 · 10× · бары OKX</p></div>
      <span className="text-xs text-amber-200 border border-amber-900 rounded px-2 py-1 self-start">СИМУЛЯЦИЯ · без ордеров</span>
    </div>
    <div className="p-4 space-y-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Сценарий FVG">
        {!hourly&&<><button aria-pressed={!alternative} onClick={()=>{setScenario("base");setSelected("");}} className={`rounded px-3 py-2 text-sm ${!alternative?"bg-cyan-700 text-white":"bg-slate-800 text-slate-300"}`}>Основная · +$10 / +$20</button>
        <button aria-pressed={alternative} onClick={()=>{setScenario("alt");setSelected("");}} className={`rounded px-3 py-2 text-sm ${alternative?"bg-violet-700 text-white":"bg-slate-800 text-slate-300"}`}>Альтернатива · +$5 / +$10</button></>}
        <a href={hourly?"/strategies/fvg-dca":"/strategies/fvg-hourly"} className="rounded px-3 py-2 text-sm bg-slate-800 text-cyan-200">{hourly?"← Дневные / недельные":"Часовая альтернатива · 1H →"}</a>
      </div>
      {alternative?<div className="rounded border border-violet-800 p-3 space-y-2">
        <h3 className="font-semibold text-violet-200">{hourly?"H1-ALT050 · отдельный forward-счёт":"ALT-050 · тейк 50%, стоп 100% маржи"}</h3>
        <p className="text-sm text-slate-300">Первый вход $10: тейк +$5 net. При −$5 добавление ещё $10; после докупки общая прибыль для закрытия +$10 net. Стоп −$10 / −$20 net; расчётная ликвидация проверяется раньше стопа и может наступить первой. Комиссии, funding и проскальзывание включены.</p>
        <p className="text-xs text-amber-300">{hourly?"Часовые FVG из трёх закрытых свечей. Только новые возвраты к A после запуска; прежние касания — baseline, без выдуманных исторических входов. Исполнение по следующему 1m open после обнаружения, без новой рассылки Telegram. Собственные $1000 и история; QNT equity исключена, объёмный фильтр не добавлен.":"Ретроспективное «если бы» на тех же принятых первых входах. Докупка и выход независимы; основной счёт не меняется. Пропуски базы не превращаются в новые входы; исторический результат не подтверждает прибыльность."}</p>
        {baseState?.alternative_error&&<p role="alert" className="text-rose-300">{baseState.alternative_error}</p>}
      </div>:<p className="text-sm text-slate-300">Первый вход: касание линии A, $10 маржи после Telegram по открытию следующей минуты. Ограничение диапазона убрано для новых сигналов; старые сделки не пересчитаны. При −$5 по первой части добавление ещё $10.
        Цель: +$10 net без добавления или +$20 net после него. Стопа нет; ликвидация расчётная.
        Комиссия 0,05% и проскальзывание 0,02% на исполнение; funding по доступной истории OKX.</p>}
      <p className="text-xs text-amber-300">Ликвидация — оценка по mark price и публичному tier; она может отличаться от фактической на личном аккаунте.
        Открытые позиции и пропуски не включены в прибыль закрытых. Доходность не доказана.</p>
      {error&&<p role="alert" className="text-rose-300">{error}. Старые цифры не выдаю за текущие.</p>}
      {!state&&!error&&<p className="text-slate-400">Загрузка FVG…</p>}
      {state&&<>
        {hourly&&<div className="rounded border border-violet-900 p-3 text-sm text-slate-300">
          Запущено {msk(state.activated_at)} МСК. Последний сбор {msk(state.last_scan?.at)} МСК; проверено {state.last_scan?.checked??0}/{state.last_scan?.universe??59}. Проверка каждые 15 минут.
          <p className="text-xs text-slate-400">Baseline зон {state.zones?.baseline??0} · ожидают касания {state.zones?.watch??0}. Больше сигналов на 1H не означает больше прибыли.</p>
          {(!state.last_scan||state.at-state.last_scan.at>20*60_000)&&<p role="alert" className="text-amber-300">Сбор часовых данных ещё не завершён или устарел; не считайте эти цифры свежими.</p>}
          {state.last_scan&&Object.keys(state.last_scan.errors).length>0&&<p role="alert" className="text-amber-300">Ошибки сбора: {Object.entries(state.last_scan.errors).map(([s,e])=>`${s}: ${e}`).join("; ")}</p>}
          {(state.last_scan?.excluded.length??0)>0&&<p className="text-amber-300">Недоступны как криптоконтракты: {state.last_scan?.excluded.join(", ")}</p>}
        </div>}
        {alternative&&state.comparison&&<div className="rounded border border-violet-900 p-3 text-sm text-slate-300">
          Одинаковых первых входов: {state.comparison.matched_entries}. Основная: закрыто {state.comparison.base_closed}, net {usd(state.comparison.base_closed_net)}. Альтернатива: закрыто {state.total.closed}, net {usd(state.total.closed_net)}. Завершены в обоих сценариях: {state.comparison.both_closed}.
          <p className="text-xs text-amber-300 mt-1">Раннее закрытие альтернативы не означает, что открытая основная сделка убыточна. Плавающий и закрытый PnL не сравниваются как окончательные исходы. Бюджет этого сценария отдельный, с основной не складывается.</p>
        </div>}
        <p className="text-sm text-cyan-200">Условие новых входов: {state.entry_condition ?? "загрузка правила"}. Цена исполнения учитывает проскальзывание.</p>
        {state.capital&&<div className="rounded-lg border border-cyan-900 bg-cyan-950/20 p-3 space-y-1">
          <h3 className="font-semibold text-cyan-100">Виртуальный бюджет · было {amount(state.capital.initial)}</h3>
          <div className="text-sm text-slate-200">Баланс после закрытых сделок: {amount(state.capital.balance)} · занято маржи: {amount(state.capital.reserved)} · свободно: {amount(state.capital.available)}</div>
          <p className="text-xs text-slate-400">Свободно = баланс − занятая маржа − оплаченные расходы открытых позиций ({amount(state.capital.open_paid_costs)}). При закрытии средства возвращаются с net результатом. Плавающая прибыль не расходуется; нехватка средств блокирует новый вход или добавление.</p>
        </div>}
        <p className="text-xs text-slate-400">«Заработано» — сумма положительных net, «Потеряно» — сумма отрицательных net по закрытым сделкам, включая расчётные ликвидации. Расходы уже учтены. Итоги за всю историю выбранного сценария; фильтры таблицы их не меняют.</p>
        {hourly?<SummaryCard title="Часовые FVG · 1H" data={state.total} positions={positions}/>:<div className="grid lg:grid-cols-3 gap-3">
          <SummaryCard title="Всего · 1D + 1W" data={state.total} positions={positions}/>
          <SummaryCard title="Дневные FVG · 1D" data={state.daily} positions={positions.filter(p=>p.frame==="1D")}/>
          <SummaryCard title="Недельные FVG · 1W" data={state.weekly} positions={positions.filter(p=>p.frame==="1W")}/>
        </div>}
        <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-300">
          Вместе: закрытые <span className="font-mono">{usd(state.total.closed_net)}</span> ·
          оценка открытых <span className="font-mono">{usd(state.total.open_net)}</span> ·
          максимум просадки закрытой кривой {amount(state.max_closed_drawdown)} ·
          худшая закрытая {usd(state.worst_closed)}. Это не единый торговый счёт.
        </div>
        {positions.length===0&&<p className="text-slate-400 text-sm">Открытых или закрытых сделок пока нет. Пропущенные сигналы скрыты; записи аудита сохранены.</p>}
        {current&&<div className="space-y-3 rounded-lg border border-slate-700 bg-slate-950/50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="font-semibold text-slate-100">График · {current.asset} · {current.frame} · {current.side==="bullish"?"🟢 ЛОНГ":"🔴 ШОРТ"}</h3>
              <p className="text-xs text-slate-400">Сигнал {msk(current.sent_at)} МСК · входы {current.fills} · {current.status}</p></div>
            <div className="flex gap-1">{(["1H","4H","1D","1W"] as const).map(x=><button key={x}
              onClick={()=>setIntervalValue(x)} className={`rounded px-3 py-2 text-xs ${interval===x?"bg-cyan-800 text-white":"bg-slate-800 text-slate-300"}`}>{x}</button>)}</div>
          </div>
          <p className="text-xs text-amber-200">Линия A: {price(Number(current.level))} · свеча-источник {msk(current.origin_ts)} МСК. {current.entry_rule==="A_UP_1_5"?"Историческая версия: её исходные правила сохранены.":"Сигнал по касанию A; цена исполнения может отличаться от A."}</p>
          {chartError&&<p role="alert" className="text-rose-300 text-sm">{chartError}</p>}
          {!chart&&!chartError&&<p className="text-slate-400 text-sm">Загрузка свечей OKX…</p>}
          {chart&&<>
            {!chart.origin_visible&&<p className="text-amber-300 text-xs">Свеча-источник A вне этого окна или не сохранена. Выберите 1D/1W для старого FVG; начало линии не подставляется к свече входа.</p>}
            {((current.entered!=null&&!chart.entry_visible)||
              (current.closed_at!=null&&!chart.candles.some(b=>b.time<=current.closed_at!-1&&current.closed_at!-1<b.time+({"1H":3600000,"4H":14400000,"1D":86400000,"1W":604800000}[interval])))||chart.clipped)&&
              <p className="text-amber-300 text-xs">Не все метки помещаются в окно {interval}. Выберите 4H, 1D или 1W; результаты сделки от масштаба не меняются.</p>}
            <FvgDcaChart key={`${current.id}:${interval}`} candles={chart.candles} position={current} interval={interval}/>
            <p className="text-xs text-slate-500">Свечи: OKX · обновлено {msk(chart.at)} МСК · <a className="text-cyan-300 hover:underline" href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${current.asset}USDT.P`)}&interval=${interval==="1H"?"60":interval==="4H"?"240":interval}`} target="_blank" rel="noopener noreferrer">🔗 Открыть в TradingView</a></p>
          </>}
          <div className="flex flex-wrap gap-3 text-xs text-slate-300">{current.events.map((e,i)=><span key={i} className="rounded bg-slate-800 px-2 py-1">
            {e.kind==="entry"?"Вход 1":e.kind==="add"?"Вход 2":e.kind==="target"?"Тейк":e.kind==="stop"?"Стоп":e.kind==="liquidation_estimate"?"Расчётная ликвидация":e.kind} · {msk(e.at)} · {price(e.price??e.mark_price)}
          </span>)}</div>
        </div>}
        {positions.length>0&&<div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-100 mr-auto">История сделок · {visible.length} из {positions.length}</h3>
            <select aria-label="Фильтр таймфрейма" className="bg-slate-800 rounded p-2 text-xs" value={frameFilter} onChange={e=>setFrameFilter(e.target.value)}><option value="all">{hourly?"1H":"1D + 1W"}</option>{!hourly&&<><option value="1D">1D</option><option value="1W">1W</option></>}</select>
            <select aria-label="Фильтр статуса" className="bg-slate-800 rounded p-2 text-xs" value={statusFilter} onChange={e=>setStatusFilter(e.target.value)}><option value="all">Все сделки</option><option value="open">Открытые</option><option value="pending">Ожидают входа</option><option value="target">Цель достигнута</option><option value="stop">Стоп</option><option value="liquidated">Ликвидация</option><option value="finished">Все завершённые</option></select>
            <select aria-label="Порядок сделок" className="bg-slate-800 rounded p-2 text-xs" value={historyOrder} onChange={e=>setHistoryOrder(e.target.value)}><option value="open_first">Открытые сверху</option><option value="target_first">Цель достигнута сверху</option><option value="recent">Последние события сверху</option></select>
          </div>
          {visible.length===0&&<p className="text-sm text-slate-400">По выбранным фильтрам сделок нет.</p>}
          <div className="max-h-[520px] overflow-auto">
          <table className="w-full text-xs text-left text-slate-300"><thead><tr className="border-b border-slate-700 text-slate-400">
            <th className="p-2">Актив / зона</th><th className="p-2">Статус</th><th className="p-2">Входы</th>
            <th className="p-2">Средняя / цель / ликв.</th><th className="p-2">Net</th><th className="p-2">Время</th>
          </tr></thead><tbody>{visible.map(p=>{
            const href=`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${p.asset}USDT.P`)}&interval=${p.frame==="1H"?"60":p.frame}`;
            const direction=p.side==="bullish"?"🟢 LONG":"🔴 SHORT";
            const details=p.events.filter(e=>e.kind==="entry"||e.kind==="add");
            return <tr key={p.id} className={`border-b border-slate-800 align-top ${current?.id===p.id?"bg-cyan-950/30":""}`}>
              <td className="p-2 whitespace-nowrap"><button onClick={()=>setSelected(p.id)} className="text-cyan-200 hover:underline font-semibold">{p.asset} · {p.frame}</button><br/>{direction}<br/>
                <span className="text-slate-500">{p.entry_rule==="A_UP_1_5"?"Историческая версия":"Касание A"}</span><br/>
                <a href={href} target="_blank" rel="noopener noreferrer" className="text-cyan-300 hover:underline">🔗 График</a></td>
              <td className="p-2">{p.status==="liquidated"?"Ликвидация (оценка)":p.status==="closed"?(closeReason(p)==="stop"?"Стоп":closeReason(p)==="target"?"Цель":"Закрыта"):p.status==="open"?"Открыта":p.status==="pending"?"Ожидает входа":"Пропущена"}
                {p.data_error&&<div className="text-amber-300">Данные устарели</div>}
                {p.status==="skipped"&&p.events.some(e=>e.reason?.startsWith("Price left"))&&<div className="text-slate-400">Цена вышла из диапазона до входа</div>}
                {p.ambiguous&&<div className="text-amber-300">Неоднозначная минута</div>}</td>
              <td className="p-2 whitespace-nowrap">{details.length?details.map((e,i)=><div key={i}>{i+1}: {price(e.price)} · {msk(e.at)}</div>):"—"}</td>
              <td className="p-2 whitespace-nowrap">{price(p.avg)}<br/>цель {price(p.target_est)}<br/>{alternative&&<>стоп {price(p.stop_est)}<br/></>}ликв. {price(p.liquidation_est)}</td>
              <td className={`p-2 font-mono ${p.net>=0?"text-emerald-300":"text-rose-300"}`}>{p.status==="skipped"?"—":usd(p.net)}<br/>
                <span className="text-slate-500">ком. {amount(p.fees)} · фонд. {usd(p.funding)}</span></td>
              <td className="p-2 whitespace-nowrap">сигнал {msk(p.sent_at)}<br/>вход {msk(p.entered)}<br/>выход {msk(p.closed_at)}</td>
            </tr>;
          })}</tbody></table>
          </div>
        </div>}
        <p className="text-xs text-slate-500">Обновлено {msk(state.at)} МСК. Одна позиция на актив; пропущенные сигналы скрыты, но сохранены для аудита. Ошибки данных не скрываются.</p>
      </>}
    </div>
  </section>;
}
