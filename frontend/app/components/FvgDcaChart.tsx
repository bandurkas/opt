"use client";
import {useEffect,useRef,useState} from "react";
import {createChart,BarSeries,LineSeries,ColorType,CrosshairMode,LineStyle,createSeriesMarkers,
  type IChartApi,type ISeriesApi,type IPriceLine,type SeriesMarker,type UTCTimestamp,type Time} from "lightweight-charts";

export type FvgChartCandle={time:number;open:number;high:number;low:number;close:number;complete:boolean};
export type FvgChartEvent={kind:string;at:number;price?:number;mark_price?:number};
export type FvgChartPosition={side:"bullish"|"bearish";avg:number;level?:string;origin_ts?:number;
  fvg_low?:string;fvg_high?:string;entry_rule?:string;entry_low?:string;entry_high?:string;
  target_est?:number;liquidation_est?:number;stop_est?:number;
  status:string;events:FvgChartEvent[]};

const fmt=(v:number)=>v.toLocaleString("ru-RU",{maximumSignificantDigits:8});
const stamp=(ms:number)=>new Date(ms).toLocaleString("ru-RU",{
  timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});

export default function FvgDcaChart({candles,position,interval}:{candles:FvgChartCandle[];
  position:FvgChartPosition;interval:"1H"|"4H"|"1D"|"1W"}){
  const host=useRef<HTMLDivElement>(null);
  const chart=useRef<IChartApi|null>(null);
  const series=useRef<ISeriesApi<"Bar">|null>(null);
  const lines=useRef<IPriceLine[]>([]);
  const fitted=useRef(false);
  const [hover,setHover]=useState("Наведите курсор на свечу: время, открытие, максимум, минимум, закрытие");
  useEffect(()=>{
    if(!host.current)return;
    const c=createChart(host.current,{autoSize:true,
      layout:{background:{type:ColorType.Solid,color:"#020617"},textColor:"#94a3b8",fontSize:12},
      grid:{vertLines:{color:"#172033"},horzLines:{color:"#172033"}},
      crosshair:{mode:CrosshairMode.Normal},
      rightPriceScale:{borderColor:"#334155",scaleMargins:{top:.12,bottom:.12}},
      timeScale:{timeVisible:true,secondsVisible:false,borderColor:"#334155",barSpacing:11,
        minBarSpacing:3,rightOffset:5,tickMarkFormatter:(t:Time)=>typeof t==="number"?
          new Date(t*1000).toLocaleDateString("ru-RU",{timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit"}):""},
      localization:{locale:"ru-RU",timeFormatter:(t:Time)=>typeof t==="number"?stamp(t*1000):String(t)},
      handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:false},
      handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true,axisDoubleClickReset:true}});
    const s=c.addSeries(BarSeries,{upColor:"#568d82",downColor:"#ac717e",thinBars:false,
      priceFormat:{type:"price",precision:8,minMove:.00000001}});
    c.subscribeCrosshairMove(p=>{const v=p.seriesData.get(s);
      if(v&&"open" in v&&typeof p.time==="number")
        setHover(`${stamp(p.time*1000)} МСК · O ${fmt(v.open)} · H ${fmt(v.high)} · L ${fmt(v.low)} · C ${fmt(v.close)}`);
    });
    chart.current=c;series.current=s;
    return()=>{c.remove();chart.current=null;series.current=null;lines.current=[];fitted.current=false;};
  },[]);
  useEffect(()=>{
    const c=chart.current,s=series.current;
    if(!c||!s||!candles.length)return;
    const prior=c.timeScale().getVisibleLogicalRange();
    s.setData(candles.map(b=>({...b,time:Math.floor(b.time/1000) as UTCTimestamp})));
    for(const line of lines.current)s.removePriceLine(line);
    lines.current=[];
    for(const [label,raw,color] of [
      ["FVG низ",position.fvg_low,"#8b5cf6"],
      ["FVG верх",position.fvg_high,"#8b5cf6"],["Средняя",position.avg,"#22d3ee"],
      ["Цель",position.target_est,"#34d399"],["Стоп −100%",position.stop_est,"#f97316"],["Ликв. оценка",position.liquidation_est,"#fb7185"]
    ] as const){
      const value=Number(raw);
      if(Number.isFinite(value)&&value>0)lines.current.push(s.createPriceLine({
        price:value,color,lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true,title:label}));
    }
    if(!fitted.current){c.timeScale().fitContent();fitted.current=true;}
    else if(prior)c.timeScale().setVisibleLogicalRange(prior);
    const candleTimes=candles.map(b=>b.time);
    const step={"1H":3600000,"4H":14400000,"1D":86400000,"1W":604800000}[interval];
    const snap=(at:number,isExit:boolean)=>{
      const target=at-(isExit?1:0);
      let left=0,right=candleTimes.length-1,result=-1;
      while(left<=right){const mid=(left+right)>>1;
        if(candleTimes[mid]<=target){result=mid;left=mid+1;}else right=mid-1;}
      return result<0||target>=candleTimes[result]+step?null:
        Math.floor(candleTimes[result]/1000) as UTCTimestamp;
    };
    const marks:SeriesMarker<UTCTimestamp>[]=[];
    const a=Number(position.level),origin=position.origin_ts;
    let aLine:ISeriesApi<"Line">|null=null;
    if(origin!=null&&Number.isFinite(a)&&a>0){
      const originTime=snap(origin,false);
      const start=originTime??(origin<candleTimes[0]?Math.floor(candleTimes[0]/1000) as UTCTimestamp:null);
      const end=Math.floor(candleTimes[candleTimes.length-1]/1000) as UTCTimestamp;
      if(start!=null){
        aLine=c.addSeries(LineSeries,{color:"#f59e0b",lineWidth:2,lineStyle:LineStyle.Dashed,
          priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false,title:"A"});
        aLine.setData(start===end?[{time:start,value:a}]:[{time:start,value:a},{time:end,value:a}]);
      }
      if(originTime!=null)marks.push({time:originTime,position:"atPriceMiddle",price:a,
        shape:"circle",color:"#f59e0b",size:.8,text:"A · свеча-источник"});
    }
    for(const e of position.events){
      if(!["entry","add","target","stop","liquidation_estimate"].includes(e.kind))continue;
      const entry=e.kind==="entry"||e.kind==="add";
      const time=snap(e.at,e.kind!=="entry");if(time==null)continue;
      const up=entry?(position.side==="bullish"):(position.side==="bearish");
      const value=e.price??e.mark_price;
      marks.push({time,position:up?"belowBar":"aboveBar",shape:up?"arrowUp":"arrowDown",
        color:entry?"#22d3ee":e.kind==="target"?"#34d399":"#fb7185",
        text:`${e.kind==="entry"?"ВХОД 1":e.kind==="add"?"ВХОД 2":e.kind==="target"?"ТЕЙК":e.kind==="stop"?"СТОП":"ЛИКВ."}${value?" "+fmt(value):""}`});
      if(value&&Number.isFinite(value))marks.push({time,position:"atPriceMiddle",price:value,
        shape:"circle",color:entry?"#7eb9c5":"#d2b57a",size:.65});
    }
    const attached=createSeriesMarkers(s,marks.sort((a,b)=>a.time-b.time),{zOrder:"top"});
    return()=>{attached.detach();if(aLine&&chart.current===c)c.removeSeries(aLine);};
  },[candles,position,interval]);
  function zoom(factor:number){const t=chart.current?.timeScale(),r=t?.getVisibleLogicalRange();
    if(!t||!r)return;const mid=(r.from+r.to)/2,half=(r.to-r.from)*factor/2;
    t.setVisibleLogicalRange({from:mid-half,to:mid+half});}
  return <div className="rounded-lg border border-slate-800 bg-slate-950 overflow-hidden">
    <div className="flex flex-wrap gap-2 p-2 items-center text-xs border-b border-slate-800">
      <button className="px-3 py-2 bg-slate-800 rounded" onClick={()=>zoom(.75)} aria-label="Приблизить">＋</button>
      <button className="px-3 py-2 bg-slate-800 rounded" onClick={()=>zoom(1.33)} aria-label="Отдалить">−</button>
      <button className="px-3 py-2 bg-slate-800 rounded" onClick={()=>chart.current?.timeScale().fitContent()}>Весь участок</button>
      <span className="text-slate-400">Перетаскивание · масштаб колесом · время МСК</span>
    </div>
    <p className="text-xs font-mono px-3 py-2 text-slate-300 min-h-8">{hover}</p>
    <div ref={host} className="h-[480px] w-full" role="region" aria-label="Интерактивный график FVG и виртуальных входов"/>
    <p className="p-2 text-[11px] text-slate-500">Бары OKX. Жёлтая линия A — от тени первой свечи FVG вправо. Пунктир: границы FVG, средняя цена, текущие расчётные цель и ликвидация.
      Стрелки и точки: модельные входы и выход. Текущая свеча может быть незакрытой; она не меняет расчёт статистики задним числом.</p>
  </div>;
}
