"use client";
import {useEffect,useRef,useState} from 'react';
import {createChart,CandlestickSeries,LineSeries,ColorType,CrosshairMode,LineStyle,createSeriesMarkers,
  type IChartApi,type ISeriesApi,type IPriceLine,type UTCTimestamp,type SeriesMarker,type Time} from 'lightweight-charts';

type Candle={time:number;open:number;high:number;low:number;close:number};
type Position={id:string;frame:string;avg:number|null;stop:number|null;t2:number|null;
  impulse?:{start_ts:number;end_ts:number;start:number;end:number};
  fills?:{kind:string;at:number;price:number;qty:number}[]};
const date=(ms:number)=>new Date(ms).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const price=(v:number)=>v.toLocaleString('ru-RU',{maximumSignificantDigits:8});

export default function N6InteractiveChart({candles,position}:{candles:Candle[];position?:Position}){
  const host=useRef<HTMLDivElement>(null),chart=useRef<IChartApi|null>(null),series=useRef<ISeriesApi<'Candlestick'>|null>(null);
  const impulseSeries=useRef<ISeriesApi<'Line'>|null>(null);
  const fitted=useRef(false),lines=useRef<IPriceLine[]>([]);
  const [hover,setHover]=useState('Наведите курсор на свечу: время, O / H / L / C');
  useEffect(()=>{
    if(!host.current)return;
    const c=createChart(host.current,{autoSize:true,
      layout:{background:{type:ColorType.Solid,color:'#020617'},textColor:'#94a3b8',fontSize:12},
      grid:{vertLines:{color:'#172033'},horzLines:{color:'#172033'}},
      crosshair:{mode:CrosshairMode.Normal},
      rightPriceScale:{borderColor:'#334155',scaleMargins:{top:.15,bottom:.15}},
      timeScale:{timeVisible:true,secondsVisible:false,borderColor:'#334155',barSpacing:12,minBarSpacing:3,rightOffset:4,
        tickMarkFormatter:(time:Time)=>typeof time==='number'?new Date(time*1000).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'}):''},
      localization:{locale:'ru-RU',timeFormatter:(time:Time)=>typeof time==='number'?date(time*1000):String(time)},
      handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
      handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true,axisDoubleClickReset:true}});
    const s=c.addSeries(CandlestickSeries,{upColor:'#568d82',downColor:'#ac717e',wickUpColor:'#568d82',wickDownColor:'#ac717e',borderVisible:false,
      priceFormat:{type:'price',precision:8,minMove:.00000001}});
    const impulse=c.addSeries(LineSeries,{color:'#a89acb',lineWidth:2,priceLineVisible:false,lastValueVisible:false,
      crosshairMarkerVisible:true,priceFormat:{type:'price',precision:8,minMove:.00000001}});
    chart.current=c;series.current=s;impulseSeries.current=impulse;
    c.subscribeCrosshairMove(p=>{const v=p.seriesData.get(s);if(v&&'open' in v&&typeof p.time==='number')setHover(`${date(p.time*1000)} МСК · O ${price(v.open)} · H ${price(v.high)} · L ${price(v.low)} · C ${price(v.close)}`);});
    return()=>{c.remove();chart.current=null;series.current=null;impulseSeries.current=null;fitted.current=false;lines.current=[];};
  },[]);
  useEffect(()=>{
    const c=chart.current,s=series.current;if(!c||!s||!candles.length)return;
    const previous=c.timeScale().getVisibleLogicalRange();
    s.setData(candles.map(b=>({...b,time:Math.floor(b.time/1000) as UTCTimestamp})));
    const impulse=position?.impulse;
    const hasImpulse=impulse&&impulse.start_ts<impulse.end_ts&&Number.isFinite(impulse.start)&&Number.isFinite(impulse.end)
      &&candles.some(b=>b.time===impulse.start_ts)&&candles.some(b=>b.time===impulse.end_ts);
    impulseSeries.current?.setData(hasImpulse?[
      {time:Math.floor(impulse.start_ts/1000) as UTCTimestamp,value:impulse.start},
      {time:Math.floor(impulse.end_ts/1000) as UTCTimestamp,value:impulse.end}
    ]:[]);
    for(const l of lines.current)s.removePriceLine(l);
    lines.current=[];
    for(const [title,value,color] of [['Вход',position?.avg,'#22d3ee'],['Стоп',position?.stop,'#fb7185'],['Цель',position?.t2,'#34d399']] as const){
      if(value!=null&&Number.isFinite(value)&&value>0)lines.current.push(s.createPriceLine({price:value,color,lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true,title}));
    }
    let initialFrame:number|undefined;
    if(!fitted.current){c.timeScale().fitContent();fitted.current=true;initialFrame=requestAnimationFrame(()=>c.timeScale().fitContent());}else if(previous)c.timeScale().setVisibleLogicalRange(previous);
    const step=(position?.frame==='30m'?30:15)*60000;
    const markers:SeriesMarker<UTCTimestamp>[]=(position?.fills??[]).filter(f=>Number.isFinite(f.price)).map<SeriesMarker<UTCTimestamp>>(f=>{
      // Exits are recorded at minute close; anchor them to the candle containing that minute.
      const t=Math.floor((f.at-(f.kind==='entry'?0:1))/step)*step;
      return {time:Math.floor(t/1000) as UTCTimestamp,position:f.kind==='entry'?'belowBar':'aboveBar',
        shape:f.kind==='entry'?'arrowUp':'arrowDown',color:f.kind==='entry'?'#22d3ee':'#fbbf24',
        text:`${f.kind==='entry'?'ВХОД':f.kind==='stop'?'СТОП':f.kind==='target'?'ТЕЙК':'ВЫХОД'} ${price(f.price)}`};
    }).filter(m=>candles.some(b=>Math.floor(b.time/1000)===m.time)).sort((a,b)=>a.time-b.time);
    const dots:SeriesMarker<UTCTimestamp>[]=(position?.fills??[]).filter(f=>Number.isFinite(f.price)).map(f=>({
      time:Math.floor(Math.floor((f.at-(f.kind==='entry'?0:1))/step)*step/1000) as UTCTimestamp,
      position:'atPriceMiddle' as const,price:f.price,shape:'circle' as const,
      color:f.kind==='entry'?'#7eb9c5':'#d2b57a',size:.65,
    })).filter(m=>candles.some(b=>Math.floor(b.time/1000)===m.time));
    const marks=createSeriesMarkers(s,[...markers,...dots].sort((a,b)=>a.time-b.time),{zOrder:'top'});
    return()=>{if(initialFrame!=null)cancelAnimationFrame(initialFrame);marks.detach();};
  },[candles,position]);
  function zoom(factor:number){const t=chart.current?.timeScale(),r=t?.getVisibleLogicalRange();if(!t||!r)return;const mid=(r.from+r.to)/2,half=(r.to-r.from)*factor/2;t.setVisibleLogicalRange({from:mid-half,to:mid+half});}
  return <div className="rounded-lg border border-slate-800 bg-slate-950 overflow-hidden">
    <div className="flex flex-wrap gap-2 p-2 items-center text-xs border-b border-slate-800"><button aria-label="Приблизить график" className="px-3 py-2 bg-slate-800 rounded" onClick={()=>zoom(.75)}>＋</button><button aria-label="Отдалить график" className="px-3 py-2 bg-slate-800 rounded" onClick={()=>zoom(1.33)}>−</button><button className="px-3 py-2 bg-slate-800 rounded" onClick={()=>{chart.current?.priceScale('right').applyOptions({autoScale:true});chart.current?.timeScale().fitContent();}}>Вся сделка</button><span className="text-slate-400">Тяни мышью · колесо — масштаб · шкала справа — высота · время МСК</span></div>
    {position?.impulse&&<p className="text-xs px-3 py-2 text-violet-300">Импульс №6 A: {price(position.impulse.start)} ({date(position.impulse.start_ts)} МСК) → {price(position.impulse.end)} ({date(position.impulse.end_ts)} МСК). Сиреневая линия — от начального свинга до экстремума BOS.</p>}
    <p className="text-xs font-mono px-3 py-2 text-slate-300 min-h-8" aria-live="off">{hover}</p>
    {!candles.length&&<p className="p-2 text-slate-500 text-xs">Загрузка свечей…</p>}
    <div ref={host} className="h-[440px] w-full" role="region" aria-label="Интерактивные свечи Bybit стратегии №6"/>
    <p className="p-2 text-[11px] text-slate-500">Сиреневая линия — исходный импульс по OKX; свечи — Bybit, поэтому небольшое расхождение цен возможно. Стрелки — свечи исполнения; маленькие кружки — цены входа (голубой) и выхода (песочный). Точное время — в карточках ниже. Масштаб сохраняется при обновлении.</p>
  </div>;
}
