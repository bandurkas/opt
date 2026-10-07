"use client";
import { useEffect, useState } from "react";
import FvgDcaChart, {type FvgChartCandle} from "./FvgDcaChart";
import {closedEconomics,closeReason,historyPositions,visiblePositions} from "./fvgDcaView";
import {EXPERIMENT_IDS,EXPERIMENTS,experimentApi,chartApi,type HourlyExperiment,type MarketTrend} from "../lib/fvgHourlyExperiments";

type Event = {kind:string;at:number;price?:number;mark_price?:number;qty?:number;fee?:number;net?:number;reason?:string};
type Position = {id:string;version?:string;asset:string;symbol:string;frame:"1H"|"1D"|"1W";side:"bullish"|"bearish";
  status:string;sent_at:number;entered?:number;closed_at?:number;fills:number;avg:number;q:number;
  margin:number;fees:number;funding:number;net:number;last?:number;marked_at?:number;
  level?:string;origin_ts?:number;fvg_low?:string;fvg_high?:string;entry_rule?:string;entry_low?:string;entry_high?:string;
  bos_at?:number;bos_level?:string;swing_ts?:number;impulse_ts?:number;choch_at?:number;confirmed_at?:number;
  a_received_at?:number;choch_level?:string;confirmation_received_at?:number;
  liquidation_est?:number;target_est?:number;stop_est?:number;target_net:number;ambiguous?:boolean;exit_reason?:string;
  data_error?:string;trend_at_signal?:MarketTrend;events:Event[]};
type Summary = {signals:number;closed:number;open:number;skipped:number;wins:number;losses:number;
  liquidations:number;closed_gross:number;closed_net:number;open_net:number;fees:number;funding:number;
  open_margin:number;open_notional:number;data_errors:number};
type State = {experiment?:HourlyExperiment;title?:string;risk_warning?:string;market_trend?:MarketTrend;entry_condition?:string;at:number;mode:string;source:string;leverage:number;margin_per_entry:number;
  total:Summary;daily:Summary;weekly:Summary;max_closed_drawdown:number;
  worst_closed:number|null;positions:Position[];capital?:{initial:number;balance:number;reserved:number;available:number;open_paid_costs:number;estimated_equity:number;pending_reserved?:number;pending_margin?:number;pending_fee_buffer?:number};
  activated_at?:number;last_scan?:{at:number;checked:number;universe:number;excluded:string[];errors:Record<string,string>}|null;
  zones?:Record<string,number>;cohorts?:Record<string,Summary>;
  alternative?:State|null;alternative_error?:string;comparison?:{matched_entries:number;base_closed:number;base_closed_net:number;both_closed:number}};
type ChartState={at:number;source:string;symbol:string;interval:"5m"|"1H"|"4H"|"1D"|"1W";
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


function currentTrendLabel(trend?:MarketTrend){return trend?.state??"UNKNOWN";}
function TrendCard({trend,label,contextOnly=false}:{trend?:MarketTrend;label:string;contextOnly?:boolean}){
  const direction=currentTrendLabel(trend);
  return <div className="rounded-lg border border-slate-700 bg-slate-950/50 p-3 space-y-1">
    <h3 className="text-sm font-semibold text-slate-100">{label}: <span className={direction==="UP"?"text-emerald-300":direction==="DOWN"?"text-rose-300":direction==="NEUTRAL"?"text-amber-300":"text-slate-400"}>{direction}</span></h3>
    <p className="text-xs text-slate-400">Получено {msk(trend?.observed_at)} МСК · последняя закрытая 4H {msk(trend?.latest4h_closed_at)} МСК</p>
    <p className="text-xs text-slate-400">Close {price(trend?.close)} · SMA50 {price(trend?.sma50)} · SMA200 {price(trend?.sma200)}</p>
    <p className="text-xs text-slate-400">UP: close и SMA50 выше SMA200; DOWN: оба ниже. Смешанные значения / равенство — NEUTRAL; недостаток 200 последовательных 4H или разрыв — UNKNOWN.</p>
    {contextOnly&&<p className="text-xs text-cyan-200">Тренд показан для контекста, не фильтр C10.</p>}
    {(!trend||trend.reason)&&<p className="text-xs text-amber-300">{trend?.reason??"Контекст не сохранён; текущий тренд не подставляется вместо тренда при сигнале."}</p>}
  </div>;
}

export default function FvgDcaPanel({hourly=false,bos:initialBos=false,mtf:initialMtf=false,rr:initialRr=false,experiment}:{hourly?:boolean;bos?:boolean;mtf?:boolean;rr?:boolean;experiment?:HourlyExperiment}){
  const [hourlyMode,setHourlyMode]=useState<"plain"|"choch"|"mtf"|"rr">(initialRr?"rr":initialMtf?"mtf":initialBos?"choch":"plain");
  const bos=hourlyMode==="choch",mtf=hourlyMode==="mtf",rr=hourlyMode==="rr";
  const api=experiment?experimentApi(experiment):rr?"/api/fvg-hourly-rr":mtf?"/api/fvg-mtf":bos?"/api/fvg-hourly-bos":hourly?"/api/fvg-hourly":"/api/fvg-dca";
  const [baseState,setState]=useState<State|null>(null);
  const [scenario,setScenario]=useState<"base"|"alt">("alt");
  const alternative=hourly||scenario==="alt";
  const state=!hourly&&alternative?baseState?.alternative??null:baseState;
  const [error,setError]=useState<string|null>(null);
  const [selected,setSelected]=useState("");
  const [interval,setIntervalValue]=useState<"5m"|"1H"|"4H"|"1D"|"1W">("1H");
  const [frameFilter,setFrameFilter]=useState("all");
  const [statusFilter,setStatusFilter]=useState("all");
  const [historyOrder,setHistoryOrder]=useState("open_first");
  const [chart,setChart]=useState<ChartState|null>(null);
  const [chartError,setChartError]=useState<string|null>(null);
  function changeHourly(next:typeof hourlyMode){
    if(next===hourlyMode)return;
    setHourlyMode(next);setState(null);setSelected("");setChart(null);setChartError(null);setError(null);
  }
  useEffect(()=>{
    setState(null);setSelected("");setChart(null);setError(null);
    let cancelled=false;const controller=new AbortController();
    async function load(){try{
      const response=await fetch(api,{cache:"no-store",signal:controller.signal});
      if(!response.ok)throw new Error("Нет связи с FVG-симулятором");
      const data=await response.json() as State;
      if(experiment&&data.experiment!==experiment)throw new Error("Ответ другого эксперимента: счёт не показан");
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
      const url=chartApi(api,current!.id,interval);
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
      <div><h2 className="font-bold text-cyan-200 text-lg">{experiment?EXPERIMENTS[experiment].title:rr?"FVG 1H · A · стоп за бар · 1:2":mtf?"FVG 1H → A → CHoCH 5m":bos?"CHoCH → подтверждение → FVG → A · 1H":hourly?"FVG A · часовая альтернатива · 1H":"FVG A · дневные и недельные"}</h2>
        <p className="text-xs text-slate-400">{experiment?`Отдельный виртуальный счёт ${experiment} · $1 000 · $10 маржи · 10×`:rr?"Касание A · бюджет $1 000 · один вход $10 · 10× · без докупки":"Касание A · бюджет $1 000 · два входа по $10 · 10× · бары OKX"}</p></div>
      <span className="text-xs text-amber-200 border border-amber-900 rounded px-2 py-1 self-start">СИМУЛЯЦИЯ · без ордеров</span>
    </div>
    <div className="p-4 space-y-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Сценарий FVG">
        {hourly&&!experiment&&<><button aria-pressed={hourlyMode==="plain"} onClick={()=>changeHourly("plain")} className={`rounded px-3 py-2 text-sm ${hourlyMode==="plain"?"bg-cyan-700 text-white":"bg-slate-800 text-slate-300"}`}>Часовая · без BOS</button>
        <button aria-pressed={mtf} onClick={()=>changeHourly("mtf")} className={`rounded px-3 py-2 text-sm ${mtf?"bg-emerald-700 text-white":"bg-slate-800 text-slate-300"}`}>FVG 1H + CHoCH 5m · 60 монет</button></>}
        {!hourly&&<>
        <button aria-pressed={alternative} onClick={()=>{setScenario("alt");setSelected("");}} className={`rounded px-3 py-2 text-sm ${alternative?"bg-violet-700 text-white":"bg-slate-800 text-slate-300"}`}>Альтернатива · +$5 / +$10</button></>}
        <a href={hourly?"/strategies/fvg-dca":"/strategies/fvg-hourly"} className="rounded px-3 py-2 text-sm bg-slate-800 text-cyan-200">{hourly?"← Дневные / недельные":"Часовая альтернатива · 1H →"}</a>
        {!hourly&&<a href="/strategies/fvg-mtf" className="rounded px-3 py-2 text-sm bg-slate-800 text-emerald-200">FVG 1H + CHoCH 5m →</a>}
        {hourly&&experiment&&<><a href="/strategies/fvg-hourly" className="rounded px-3 py-2 text-sm bg-slate-800 text-cyan-200">Часовая · без BOS</a>
          <a href="/strategies/fvg-mtf" className="rounded px-3 py-2 text-sm bg-slate-800 text-emerald-200">FVG 1H + CHoCH 5m</a></>}
        {hourly&&EXPERIMENT_IDS.map(id=><a key={id} href={EXPERIMENTS[id].path} aria-current={experiment===id?"page":undefined}
          className={`rounded px-3 py-2 text-sm ${experiment===id?"bg-violet-700 text-white":"bg-slate-800 text-violet-200"}`}>{EXPERIMENTS[id].label}</a>)}
      </div>
      {experiment?<div className="rounded border border-violet-800 p-3 space-y-2">
        <h3 className="font-semibold text-violet-200">{state?.title??EXPERIMENTS[experiment].title} · собственные $1000</h3>
        <p className="text-sm text-slate-300">{experiment==="C10"?"Один вход $10 маржи, 10×. Без докупки: цель +$5 net, стоп −$10 net. BTC 4H: тренд показан для контекста, не фильтр.":"Первый вход $10 маржи, 10×: цель +$5 net, стоп −$10 net. При −$5 net — одна докупка $10; после неё цель +$10 net, стоп −$20 net. BTC 4H: LONG только UP, SHORT только DOWN; NEUTRAL / UNKNOWN — без нового входа. Смена тренда сама по себе не закрывает позицию."}</p>
        {experiment==="C21P15"&&<p className="text-sm text-slate-300">Объём средней свечи FVG в котируемой валюте ≥ 1,5 среднего 20 предшествующих закрытых часовых свечей (без самой средней свечи). После 24 часов: если на завершённой минуте UTC15m net ≤ 0, выход планируется по первому 1m open строго после фактического получения решения. Докупка после 24 часов или запланированного выхода запрещена.</p>}
        <p className="text-xs text-slate-400">Новое касание A после записи ожидания; исполнение по следующему 1m open строго после получения сигнала, не по A. Комиссии 0,05%, проскальзывание 0,02% на исполнение и фактический funding включены. $10 — маржа, не гарантированный предел риска; расчётная ликвидация может наступить раньше стопа.</p>
        <p className="text-xs text-amber-300">{state?.risk_warning??"Forward-симуляция, не доказанная прибыльная стратегия. Новая история начинается с запуска: старые исследовательские сделки и деньги не переносятся. Каждый счёт самостоятельный; бюджеты и результаты трёх экспериментов не складываются."}</p>
      </div>:rr?<div className="rounded border border-amber-800 p-3 space-y-2">
        <h3 className="font-semibold text-amber-200">H1-A-CANDLE-RR2 · отдельный forward-эксперимент · $1000</h3>
        <p className="text-sm text-slate-300">Один вход $10 маржи, 10×, без докупки. Long: стоп ниже low первой свечи FVG на 1 тик; short: выше её high на 1 тик. Тейк вдвое дальше стопа от фактического входа — 1:2 по цене. Уровни фиксируются при входе; комиссии, funding и проскальзывание уменьшают чистое соотношение.</p>
        <p className="text-xs text-amber-300">$10 — маржа, не фиксированный риск. Сигнал при новом касании A, исполнение по следующему 1m open, не искусственно по A. Стоп за расчётной ликвидацией — пропуск. Собственная история начинается с запуска, старые сделки не переносятся. Прежний исторический пилот дал net −$41,95: преимущество не доказано.</p>
      </div>:alternative?<div className="rounded border border-violet-800 p-3 space-y-2">
        <h3 className="font-semibold text-violet-200">{mtf?"H1-A-CHOCH5M-ALT050 · отдельный forward-счёт $1000":bos?"H1-CHOCH-ALT050 · прежний счёт $1000, новая версия":hourly?"H1-ALT050 · отдельный forward-счёт":"ALT-050 · тейк 50%, стоп 100% маржи"}</h3>
        <p className="text-sm text-slate-300">Первый вход $10: тейк +$5 net. При −$5 добавление ещё $10; после докупки общая прибыль для закрытия +$10 net. Стоп −$10 / −$20 net; расчётная ликвидация проверяется раньше стопа и может наступить первой. Комиссии, funding и проскальзывание включены.</p>
        <p className="text-xs text-amber-300">{mtf?"60 криптомонет: прежние 59 + SAND, список заморожен. После нового касания A часового FVG ждём до 60 минут: CHoCH 5m против тренда по двум известным high и low (свинги 2+2), первое закрытие за уровнем и следующее строго за ним. Тень, равенство или возврат вторым закрытием отменяют этот сигнал. Пробой должен начаться после обнаружения A. Вход по следующему 1m open после получения подтверждения, не по A. Проверка раз в 15 минут, поэтому есть задержка. Telegram — только результат закрытия в отдельной рассылке. Объёмного фильтра и правила 20% нет; исторический пилот не доказал преимущество.":bos?"CHoCH против подтверждённого тренда: два последних свинга high и low понижаются для LONG или повышаются для SHORT; свинги 2+2 известны до пробоя. Первое закрытие 1H за уровнем и следующее закрытие строго за ним. Тень, равенство или возврат вторым закрытием — не входить. Последний FVG импульса фиксируется на CHoCH; касание A до подтверждения — пропуск. Затем только новый откат к A после записи ожидания. Ранее возникшие CHoCH — baseline. Старые BOS-сделки и общий бюджет сохранены, их статистика отдельная; объёмного фильтра и правила 20% нет. Фильтр не исключает все будущие ложные пробои.":hourly?"Часовые FVG из трёх закрытых свечей. Только новые возвраты к A после запуска; прежние касания — baseline, без выдуманных исторических входов. Исполнение по следующему 1m open после обнаружения, без новой рассылки Telegram. Собственные $1000 и история; QNT equity исключена, объёмный фильтр не добавлен.":"Ретроспективное «если бы» на тех же принятых первых входах. Докупка и выход независимы; основной счёт не меняется. Пропуски базы не превращаются в новые входы; исторический результат не подтверждает прибыльность."}</p>
        {baseState?.alternative_error&&<p role="alert" className="text-rose-300">{baseState.alternative_error}</p>}
      </div>:<p className="text-sm text-slate-300">Первый вход: касание линии A, $10 маржи после Telegram по открытию следующей минуты. Ограничение диапазона убрано для новых сигналов; старые сделки не пересчитаны. При −$5 по первой части добавление ещё $10.
        Цель: +$10 net без добавления или +$20 net после него. Стопа нет; ликвидация расчётная.
        Комиссия 0,05% и проскальзывание 0,02% на исполнение; funding по доступной истории OKX.</p>}
      <p className="text-xs text-amber-300">Ликвидация — оценка по mark price и публичному tier; она может отличаться от фактической на личном аккаунте.
        Открытые позиции и пропуски не включены в прибыль закрытых. Доходность не доказана.</p>
      {error&&<p role="alert" className="text-rose-300">{error}. Старые цифры не выдаю за текущие.</p>}
      {!state&&!error&&<p className="text-slate-400">Загрузка FVG…</p>}
      {state&&<>
        {experiment&&<TrendCard trend={state.market_trend} label="BTC 4H · текущий контекст" contextOnly={experiment==="C10"}/>}
        {hourly&&<div className="rounded border border-violet-900 p-3 text-sm text-slate-300">
          Запущено {msk(state.activated_at)} МСК. Последний сбор {msk(state.last_scan?.at)} МСК; проверено {state.last_scan?.checked??0}/{state.last_scan?.universe??(mtf?60:59)}. Проверка каждые 15 минут.
          <p className="text-xs text-slate-400">Baseline зон {state.zones?.baseline??0} · ожидают касания {state.zones?.watch??0}. Больше сигналов на 1H не означает больше прибыли.</p>
          {bos&&<p className="text-xs text-slate-400">Ждут второго закрытия: {state.zones?.confirmation??0} · отклонены: {state.zones?.rejected??0} · отменены новым CHoCH: {state.zones?.superseded??0} · разрыв истории: {state.zones?.gapped??0}. Это не закрытые убытки.</p>}
          {mtf&&<p className="text-xs text-slate-400">После A ждут CHoCH 5m: {state.zones?.await_choch??0} · отклонены: {state.zones?.rejected??0} · истекли: {state.zones?.expired??0}. Ожидания и отказы — не закрытые убытки.</p>}
          {(!state.last_scan||state.at-state.last_scan.at>20*60_000)&&<p role="alert" className="text-amber-300">Сбор часовых данных ещё не завершён или устарел; не считайте эти цифры свежими.</p>}
          {state.last_scan&&Object.keys(state.last_scan.errors).length>0&&<p role="alert" className="text-amber-300">Ошибки сбора: {Object.entries(state.last_scan.errors).map(([s,e])=>`${s}: ${e}`).join("; ")}</p>}
          {(state.last_scan?.excluded.length??0)>0&&<p className="text-amber-300">Недоступны как криптоконтракты: {state.last_scan?.excluded.join(", ")}</p>}
        </div>}
        {!experiment&&alternative&&state.comparison&&<div className="rounded border border-violet-900 p-3 text-sm text-slate-300">
          Одинаковых первых входов: {state.comparison.matched_entries}. Основная: закрыто {state.comparison.base_closed}, net {usd(state.comparison.base_closed_net)}. Альтернатива: закрыто {state.total.closed}, net {usd(state.total.closed_net)}. Завершены в обоих сценариях: {state.comparison.both_closed}.
          <p className="text-xs text-amber-300 mt-1">Раннее закрытие альтернативы не означает, что открытая основная сделка убыточна. Плавающий и закрытый PnL не сравниваются как окончательные исходы. Бюджет этого сценария отдельный, с основной не складывается.</p>
        </div>}
        <p className="text-sm text-cyan-200">Условие новых входов: {state.entry_condition ?? "загрузка правила"}. Цена исполнения учитывает проскальзывание.</p>
        {state.capital&&<div className="rounded-lg border border-cyan-900 bg-cyan-950/20 p-3 space-y-1">
          <h3 className="font-semibold text-cyan-100">{experiment?`Отдельный счёт ${experiment} · бюджет`:"Виртуальный бюджет"} · было {amount(state.capital.initial)}</h3>
          <div className="text-sm text-slate-200">Баланс после закрытых сделок: {amount(state.capital.balance)} · занято маржи: {amount(state.capital.reserved)} · свободно: {amount(state.capital.available)}</div>
          {experiment&&<p className="text-xs text-slate-300">Резерв ожидающих входов {amount(state.capital.pending_reserved??0)} (маржа {amount(state.capital.pending_margin??0)} · буфер комиссии {amount(state.capital.pending_fee_buffer??0)}, ещё не оплачены).</p>}
          <p className="text-xs text-slate-400">Свободно = баланс − занятая маржа − оплаченные расходы открытых позиций ({amount(state.capital.open_paid_costs)}){experiment&&<> − резерв ожидающих входов ({amount(state.capital.pending_reserved??0)})</>}. При закрытии средства возвращаются с net результатом. Плавающая прибыль не расходуется; нехватка средств блокирует новый вход или добавление.</p>
        </div>}
        <p className="text-xs text-slate-400">«Заработано» — сумма положительных net, «Потеряно» — сумма отрицательных net по закрытым сделкам, включая расчётные ликвидации. Расходы уже учтены. Итоги за всю историю выбранного сценария; фильтры таблицы их не меняют.</p>
        {bos&&state.cohorts?<div className="grid lg:grid-cols-2 gap-3">
          <SummaryCard title="Новая версия · CHoCH + FVG" data={state.cohorts["FVG-H1-CHOCH-ALT050-v1"]} positions={positions.filter(p=>p.version==="FVG-H1-CHOCH-ALT050-v1")}/>
          <SummaryCard title="Историческая версия · BOS + FVG" data={state.cohorts["FVG-H1-BOS-ALT050-v1"]} positions={positions.filter(p=>p.version==="FVG-H1-BOS-ALT050-v1")}/>
        </div>:hourly?<SummaryCard title={experiment?`${experiment} · только этот счёт`:mtf?"FVG 1H + CHoCH 5m":"Часовые FVG · 1H"} data={state.total} positions={positions}/>:<div className="grid lg:grid-cols-3 gap-3">
          <SummaryCard title="Всего · 1D + 1W" data={state.total} positions={positions}/>
          <SummaryCard title="Дневные FVG · 1D" data={state.daily} positions={positions.filter(p=>p.frame==="1D")}/>
          <SummaryCard title="Недельные FVG · 1W" data={state.weekly} positions={positions.filter(p=>p.frame==="1W")}/>
        </div>}
        <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm text-slate-300">
          {experiment?`Только счёт ${experiment}: закрытые `:bos?"Счёт вместе (CHoCH + исторические BOS): закрытые ":"Вместе: закрытые "}<span className="font-mono">{usd(state.total.closed_net)}</span> ·
          оценка открытых <span className="font-mono">{usd(state.total.open_net)}</span> ·
          максимум просадки закрытой кривой {amount(state.max_closed_drawdown)} ·
          худшая закрытая {usd(state.worst_closed)}. {experiment?"С другими экспериментами не объединяется.":"Это не единый торговый счёт."}
        </div>
        {positions.length===0&&<p className="text-slate-400 text-sm">Открытых или закрытых сделок пока нет. Пропущенные сигналы скрыты; записи аудита сохранены.</p>}
        {current&&<div className="space-y-3 rounded-lg border border-slate-700 bg-slate-950/50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="font-semibold text-slate-100">График · {current.asset} · {current.frame} · {current.side==="bullish"?"🟢 ЛОНГ":"🔴 ШОРТ"}</h3>
              <p className="text-xs text-slate-400">Сигнал {msk(current.sent_at)} МСК · входы {current.fills} · {current.status}</p></div>
            <div className="flex flex-wrap gap-1" aria-label="Таймфрейм графика">{(["5m","1H","4H","1D","1W"] as const).map(x=><button key={x}
              aria-pressed={interval===x} onClick={()=>setIntervalValue(x)} className={`rounded px-3 py-2 text-xs ${interval===x?"bg-cyan-800 text-white":"bg-slate-800 text-slate-300"}`}>{x}</button>)}</div>
          </div>
          {experiment&&<TrendCard trend={current.trend_at_signal} label="BTC 4H · зафиксирован при сигнале" contextOnly={experiment==="C10"}/>}
          <p className="text-xs text-amber-200">Линия A: {price(Number(current.level))} · свеча-источник {msk(current.origin_ts)} МСК. {current.entry_rule==="A_UP_1_5"?"Историческая версия: её исходные правила сохранены.":"Сигнал по касанию A; цена исполнения может отличаться от A."}</p>
          {current.bos_at!=null&&<p className="text-xs text-violet-200">{current.choch_at!=null?"CHoCH":"Исторический BOS"} {msk(current.bos_at)} МСК · пробитый уровень {price(Number(current.bos_level))} · свинг {msk(current.swing_ts)} · начало импульса {msk(current.impulse_ts)}.{current.confirmed_at!=null&&<> Второе закрытие {msk(current.confirmed_at)} МСК.</>} Исполнение — следующий 1m open с проскальзыванием.</p>}
          {mtf&&current.a_received_at!=null&&<p className="text-xs text-emerald-200">A обнаружено {msk(current.a_received_at)} МСК · CHoCH 5m {msk(current.choch_at)} · уровень {price(Number(current.choch_level))} · второе закрытие {msk(current.confirmed_at)} · получено {msk(current.confirmation_received_at)}. Исполнение — следующий 1m open с проскальзыванием.</p>}
          {chartError&&<p role="alert" className="text-rose-300 text-sm">{chartError}</p>}
          {!chart&&!chartError&&<p className="text-slate-400 text-sm">Загрузка свечей OKX…</p>}
          {chart&&<>
            {!chart.origin_visible&&<p className="text-amber-300 text-xs">Свеча-источник A вне этого окна или не сохранена. Выберите 1D/1W для старого FVG; начало линии не подставляется к свече входа.</p>}
            {((current.entered!=null&&!chart.entry_visible)||
              (current.closed_at!=null&&!chart.candles.some(b=>b.time<=current.closed_at!-1&&current.closed_at!-1<b.time+({"5m":300000,"1H":3600000,"4H":14400000,"1D":86400000,"1W":604800000}[interval])))||chart.clipped)&&
              <p className="text-amber-300 text-xs">Не все метки помещаются в окно {interval}. Выберите 4H, 1D или 1W; результаты сделки от масштаба не меняются.</p>}
            <FvgDcaChart key={`${current.id}:${interval}`} candles={chart.candles} position={current} interval={interval}/>
            <p className="text-xs text-slate-500">Свечи: OKX · обновлено {msk(chart.at)} МСК · <a className="text-cyan-300 hover:underline" href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(`OKX:${current.asset}USDT.P`)}&interval=${interval==="5m"?"5":interval==="1H"?"60":interval==="4H"?"240":interval}`} target="_blank" rel="noopener noreferrer">🔗 Открыть в TradingView</a></p>
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
                <span className="text-slate-500">{p.entry_rule==="H1_CHOCH_CONFIRMED_A_TOUCH"?"CHoCH → 2 закрытия → FVG → A":p.entry_rule==="H1_BOS_IMPULSE_A_TOUCH"?"Исторический BOS → FVG → A":p.entry_rule==="A_UP_1_5"?"Историческая версия":"Касание A"}</span><br/>
                {experiment&&<><span className="text-slate-400">BTC 4H при сигнале: {currentTrendLabel(p.trend_at_signal)}</span><br/></>}
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
