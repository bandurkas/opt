const ts=require('typescript'),fs=require('fs'),Module=require('module'),assert=require('node:assert/strict');
const m=new Module('economics');m._compile(ts.transpileModule(fs.readFileSync('/test/n6Economics.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,'economics.js');
const {entryEconomics,economicView}=m.exports;
const p={status:'closed',sign:1,stop:99,t2:103,net:100,fills:[{kind:'entry',at:1,price:100,qty:2}]};
assert(entryEconomics(p)>2);assert(entryEconomics({...p,sign:-1,stop:101,t2:97})>2);
assert(entryEconomics({...p,t2:100.01})<0);
assert.equal(entryEconomics({...p,fills:[]}),null);
assert.equal(entryEconomics({...p,stop:101}),null);
assert.equal(entryEconomics({...p,net:-999}),entryEconomics(p));
const low={...p,t2:101,net:-50},unknown={...p,fills:[]},open={...p,status:'open',net:30};
const before=JSON.stringify([p,low,unknown,open]);const s=economicView([p,low,unknown,open]);
assert.equal(s.closed,1);assert.equal(s.open,1);assert.equal(s.closed_net,100);assert.equal(s.open_net,30);
assert.equal(s.skipped,1);assert.equal(s.unknown,1);assert.equal(before,JSON.stringify([p,low,unknown,open]));
if(fs.existsSync('/test/economics-fixtures.json')){
 const rows=JSON.parse(fs.readFileSync('/test/economics-fixtures.json'));
 for(const r of rows)assert(Math.abs(entryEconomics(r.position)-r.ratio)<1e-9);
 console.log('Historical parity:',rows.length);
}
console.log('Economic view tests passed');
