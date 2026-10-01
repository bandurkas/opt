import test from 'node:test';
import assert from 'node:assert/strict';
import {closedEconomics,historyPositions} from './fvgDcaView.ts';

const p=(id,status,net=0,extra={})=>({id,status,net,frame:'1D',sent_at:100,...extra});
const rows=[p('target','closed',5,{exit_reason:'target',closed_at:150}),
  p('stop','closed',-10,{exit_reason:'stop',closed_at:200}),
  p('liq','liquidated',-20,{closed_at:300}),
  p('open','open',999,{sent_at:120}),p('pending','pending',999),
  p('skip','skipped',999),p('unknown-exit','closed',0)];

test('final net only: costs not double-counted and open float excluded',()=>{
  assert.deepEqual(closedEconomics(rows),{earned:5,lost:30,net:-25,missing:0});
  assert.deepEqual(closedEconomics([]),{earned:0,lost:0,net:0,missing:0});
});
test('missing final net is explicit, not zero',()=>{
  assert.equal(closedEconomics([p('a','closed',null),p('b','liquidated',NaN),p('c','closed',Infinity)]).missing,3);
});
test('base and alternative results never mix',()=>{
  assert.equal(closedEconomics([p('a','liquidated',-20)]).lost,20);
  assert.deepEqual(closedEconomics([p('a','closed',5),p('b','closed',10)]),{earned:15,lost:0,net:15,missing:0});
});
test('targets exclude stops and unknown closed reasons',()=>{
  assert.deepEqual(historyPositions(rows,'all','target','recent').map(p=>p.id),['target']);
  assert.deepEqual(historyPositions(rows,'all','stop','recent').map(p=>p.id),['stop']);
});
test('base target events work without alternative exit_reason',()=>{
  const base=[p('base','closed',10,{events:[{kind:'entry'},{kind:'target'}]}),
    p('stop','closed',-10,{exit_reason:'stop',events:[{kind:'target'}]})];
  assert.deepEqual(historyPositions(base,'all','target','target_first').map(p=>p.id),['base']);
});
test('open first preserves all non-skipped rows and does not mutate input',()=>{
  const before=rows.map(p=>p.id);
  const sorted=historyPositions(rows,'all','all','open_first');
  assert.deepEqual(sorted.slice(0,2).map(p=>p.id),['open','pending']);
  assert.equal(sorted.length,rows.length-1);
  assert.deepEqual(rows.map(p=>p.id),before);
});
test('target first and chronology are distinct orders',()=>{
  assert.equal(historyPositions(rows,'all','all','target_first')[0].id,'target');
  assert.equal(historyPositions(rows,'all','all','recent')[0].id,'liq');
});
test('timeframe and status filters combine; empty filters work',()=>{
  const mixed=[...rows,p('weekly','open',-1,{frame:'1W'})];
  assert.deepEqual(historyPositions(mixed,'1W','open','open_first').map(p=>p.id),['weekly']);
  assert.deepEqual(historyPositions(mixed,'1W','target','target_first'),[]);
  assert.equal(historyPositions(rows,'all','finished','recent').length,4);
});
