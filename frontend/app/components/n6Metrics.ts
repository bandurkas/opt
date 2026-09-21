export type MetricPosition = {status:string; net:number|null; fees:number|null; funding:number|null; entered:number|null; closed_at:number|null; frame:string; sign:number};
export function metrics(rows:MetricPosition[]) {
  const closed=rows.filter(p=>p.status==='closed'&&p.net!=null&&Number.isFinite(p.net));
  const sum=(xs:MetricPosition[])=>xs.reduce((s,p)=>s+(p.net??0),0);
  const winners=closed.filter(p=>p.net!>0), losers=closed.filter(p=>p.net!<0);
  const gain=sum(winners), loss=-sum(losers);
  const ordered=[...closed].sort((a,b)=>(a.closed_at??0)-(b.closed_at??0));
  let total=0,peak=0,drawdown=0;
  const curve=[{time:ordered[0]?.entered??0,value:0}];
  for(let i=0;i<ordered.length;) {
    const time=ordered[i].closed_at??0;
    do {total+=ordered[i].net??0;i++;} while(i<ordered.length&&(ordered[i].closed_at??0)===time);
    peak=Math.max(peak,total);drawdown=Math.max(drawdown,peak-total);curve.push({time,value:total});
  }
  const durations=closed.filter(p=>p.entered!=null&&p.closed_at!=null&&p.closed_at>=p.entered);
  return {count:closed.length,missing:rows.filter(p=>p.status==='closed').length-closed.length,total,
    wins:winners.length,losses:losers.length,flat:closed.length-winners.length-losers.length,
    winRate:closed.length?100*winners.length/closed.length:null,
    profitFactor:loss>0?gain/loss:null, noLoss:loss===0&&gain>0,
    average:closed.length?total/closed.length:null,
    best:closed.length?Math.max(...closed.map(p=>p.net!)):null,
    worst:closed.length?Math.min(...closed.map(p=>p.net!)):null,
    fees:closed.every(p=>p.fees!=null)?closed.reduce((s,p)=>s+p.fees!,0):null,
    funding:closed.every(p=>p.funding!=null)?closed.reduce((s,p)=>s+p.funding!,0):null,
    hours:durations.length?durations.reduce((s,p)=>s+p.closed_at!-p.entered!,0)/durations.length/3600000:null,
    drawdown,curve};
}
