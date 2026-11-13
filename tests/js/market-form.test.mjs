import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../../sbsm/static/app.js',import.meta.url),'utf8');
const handler=source.slice(source.indexOf('  if (target.id === "market-test"'),source.indexOf('  if(target.id==="telegram-test")'));
const refresh=source.slice(source.indexOf('async function refresh()'),source.indexOf('\nfunction showToast'));
const form=source.slice(source.indexOf('const marketForm ='),source.indexOf('\nfunction testAlertsCard'));

function context(api){
  const elements={};
  for(const id of ['market-provider','market-symbol','market-result','market-test','market-activate'])elements[id]={id,value:id==='market-provider'?'dhan':id==='market-symbol'?'POLYCAB':'',disabled:false,textContent:''};
  const ctx={state:{source:'account',loading:false},delays:[],api,AbortSignal,URLSearchParams,Promise,
    $:selector=>elements[selector.slice(1)],document:{getElementById:id=>elements[id]},h:String,
    badge:String,date:()=> 'market timestamp',render:()=>{},elements};
  ctx.setTimeout=(resolve,ms)=>{ctx.delays.push(ms);resolve();};
  vm.createContext(ctx);
  vm.runInContext(form+'\n'+refresh+'\nasync function exercise(){const target=elements["market-test"];'+handler+'}\nfunction snapshot(){return {...marketForm};}',ctx);
  return ctx;
}

test('Dhan is selected by default and pending form fields are disabled',()=>{
  const ctx=context(()=>{});
  vm.runInContext('marketForm.busy=true;html=marketDataCard({providers:{dhan:true,kite:false},feed_mode:"kite"})',ctx);
  assert.match(ctx.html,/<option value="dhan" selected>/);
  assert.match(ctx.html,/<select id="market-provider" disabled>/);
  assert.match(ctx.html,/id="market-symbol" disabled/);
});

test('background refresh does not replace a pending quote form',async()=>{
  let calls=0;
  const ctx=context(()=>{calls++;throw Error('unexpected request');});
  vm.runInContext('marketForm.busy=true',ctx);
  await ctx.refresh();
  assert.equal(calls,0);
});

test('rate-limit response retries automatically and preserves symbol/provider',async()=>{
  let calls=0;
  const ctx=context(async path=>{
    calls++;
    assert.match(path,/provider=dhan/);
    assert.match(path,/symbol=POLYCAB/);
    if(calls===1)throw Object.assign(Error('Broker busy'),{status:429,retryAfter:3});
    return {provider:'dhan',symbol:'POLYCAB',fresh:true,quote:{last_price:100,bid:99,ask:100,bid_quantity:10,ask_quantity:10,asof_ms:1}};
  });
  await ctx.exercise();
  assert.equal(calls,2);
  assert.deepEqual(ctx.delays,[3000]);
  const saved=ctx.snapshot();
  assert.equal(saved.provider,'dhan');assert.equal(saved.symbol,'POLYCAB');assert.equal(saved.busy,false);
  assert.match(saved.result,/DHAN.*POLYCAB.*Fresh/);
  assert.equal(ctx.elements['market-provider'].disabled,false);
});

test('repeated throttling stops after bounded retries and re-enables controls',async()=>{
  let calls=0;
  const ctx=context(async()=>{calls++;throw Object.assign(Error('rate limited'),{status:429,retryAfter:3});});
  await ctx.exercise();
  assert.equal(calls,3);
  assert.equal(ctx.delays.length,2);
  assert.equal(ctx.snapshot().busy,false);
  assert.equal(ctx.elements['market-test'].disabled,false);
  assert.equal(ctx.snapshot().result,'rate limited');
});
