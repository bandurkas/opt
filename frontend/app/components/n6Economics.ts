type EconomicPosition = {
  status:string; sign:number; stop:number|null; t2:number|null;
  net:number|null; fills?:{kind:string;at:number;price:number;qty:number}[];
};
export function entryEconomics(p:EconomicPosition):number|null {
  const entries=p.fills?.filter(f=>f.kind==='entry')??[];
  if(entries.length!==1 || ![1,-1].includes(p.sign))return null;
  const e=entries[0].price,s=p.stop,t=p.t2,sg=p.sign;
  if(s==null||t==null||![e,s,t].every(v=>Number.isFinite(v)&&v>0))return null;
  if(sg*(e-s)<=0||sg*(t-e)<=0)return null;
  const sf=s*(1-sg*.0002),tf=t*(1-sg*.0002);
  const risk=sg*(e-sf)+.0005*(e+sf);
  const reward=sg*(tf-e)-.0005*(e+tf);
  return risk>0?reward/risk:null;
}
export function economicView<T extends EconomicPosition>(positions:T[]) {
  const eligible=positions.filter(p=>p.status==='closed'||p.status==='open');
  const selected=eligible.filter(p=>(entryEconomics(p)??-Infinity)>=2);
  const closed=selected.filter(p=>p.status==='closed'),opened=selected.filter(p=>p.status==='open');
  return {positions:selected,closed:closed.length,open:opened.length,
    wins:closed.filter(p=>(p.net??0)>0).length,losses:closed.filter(p=>(p.net??0)<0).length,
    closed_net:closed.reduce((a,p)=>a+(p.net??0),0),open_net:opened.reduce((a,p)=>a+(p.net??0),0),
    skipped:eligible.filter(p=>entryEconomics(p)!=null&&(entryEconomics(p)??0)<2).length,
    unknown:eligible.filter(p=>entryEconomics(p)==null).length,
    pending:positions.filter(p=>p.status==='pending').length};
}
