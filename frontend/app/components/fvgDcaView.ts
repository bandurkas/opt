export function visiblePositions<T extends {status:string}>(positions:T[]):T[]{
  return positions.filter(p=>p.status!=="skipped");
}
