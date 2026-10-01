export function visiblePositions<T extends {status:string}>(positions:T[]):T[]{
  return positions.filter(p=>p.status!=="skipped");
}

type ViewPosition={status:string;frame:string;net:number|null;exit_reason?:string;events?:{kind:string}[];
  sent_at:number;closed_at?:number|null};

// Final net already includes the simulation's costs. Never subtract them twice.
export function closedEconomics(positions:ReadonlyArray<{status:string;net:number|null}>){
  let earned=0,lost=0,missing=0;
  for(const p of positions){
    if(p.status!=="closed"&&p.status!=="liquidated")continue;
    if(p.net==null||!Number.isFinite(p.net)){missing++;continue;}
    if(p.net>0)earned+=p.net;
    else if(p.net<0)lost-=p.net;
  }
  return {earned,lost,net:earned-lost,missing};
}

export function closeReason(p:{exit_reason?:string;events?:{kind:string}[]}){
  return p.exit_reason??p.events?.findLast(e=>e.kind==="target"||e.kind==="stop")?.kind;
}

export function isTarget(p:{status:string;exit_reason?:string;events?:{kind:string}[]}){
  return p.status==="closed"&&closeReason(p)==="target";
}

export function historyPositions<T extends ViewPosition>(positions:T[],frame:string,status:string,order:string):T[]{
  const filtered=visiblePositions(positions).filter(p=>(frame==="all"||p.frame===frame)&&
    (status==="all"||(status==="target"?isTarget(p):status==="stop"?
      p.status==="closed"&&closeReason(p)==="stop":status==="finished"?
      p.status==="closed"||p.status==="liquidated":p.status===status)));
  const rank=(p:T)=>order==="target_first"?
    (isTarget(p)?0:p.status==="open"?1:p.status==="pending"?2:3):
    (p.status==="open"?0:p.status==="pending"?1:2);
  return filtered.sort((a,b)=>{
    if(order==="open_first"||order==="target_first"){
      const group=rank(a)-rank(b);if(group)return group;
    }
    // Stable chronological tie-break; sort the filtered copy, not API state.
    return (b.closed_at??b.sent_at)-(a.closed_at??a.sent_at);
  });
}
