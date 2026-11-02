"use strict";

const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const h = escapeHTML;
const money = (value, digits = 0) => value == null ? "—" : new Intl.NumberFormat("en-IN", {style:"currency", currency:"INR", minimumFractionDigits:digits, maximumFractionDigits:digits}).format(value / 100);
const signedMoney = (value, digits=0) => value == null ? "—" : `${value > 0 ? "+" : ""}${money(value, digits)}`;
const pnlClass = value => value > 0 ? "positive" : value < 0 ? "negative" : "";
const date = (value) => value ? new Intl.DateTimeFormat("en-IN", {day:"2-digit", month:"short", hour:"2-digit", minute:"2-digit", hour12:false, timeZone:"Asia/Kolkata"}).format(value) : "—";
const time = value => new Intl.DateTimeFormat("en-IN", {hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false, timeZone:"Asia/Kolkata"}).format(value);
const human = value => String(value || "—").toLowerCase().replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
const views = {
  overview:["Overview","Portfolio overview","Every signal, position and allocation in one place."],
  positions:["Positions","Open positions","Holdings stay open until a matching exit signal or your manual close request."],
  orders:["Order book","Order book","Track reservations, partial fills and execution outcomes."],
  fills:["Execution fills","Execution fills","Individual paper fills, execution prices and modeled charges."],
  signals:["Signal inbox","Signal inbox","The durable receipt sequence behind every trading decision."],
  skipped:["Skipped signals","Skipped signals","Every skipped entry has a reason. These signals are never replayed."],
  history:["Trade history","Trade history","Closed trade cycles and realized results after modeled costs."],
  ledger:["Fund ledger","Fund ledger","An audit trail of cash, reservations, settlement and realized P&L."],
  system:["System health","System health","Check the worker, market-data connection and entry controls."]
};
const state = {source:"demo", authenticated:false, csrf:null, view:"overview", data:null, search:"", loading:false, lastUpdated:0, error:"", limit:100};

function demoData() {
  const now=Date.now();
  const positions=[
    {symbol:"POLYCAB",cycle_id:"demo:POLYCAB:1",quantity:9,cost_paise:5983000,market_value_paise:6075000,unrealized_paise:92000},
    {symbol:"ADANIENT",cycle_id:"demo:ADANIENT:1",quantity:25,cost_paise:6120000,market_value_paise:6097500,unrealized_paise:-22500},
    {symbol:"ICICIBANK",cycle_id:"demo:ICICIBANK:1",quantity:45,cost_paise:6050000,market_value_paise:6142500,unrealized_paise:92500},
    {symbol:"BEL",cycle_id:"demo:BEL:1",quantity:152,cost_paise:5900000,market_value_paise:5988800,unrealized_paise:88800}
  ].map(p=>({...p,quote_stale:false,quote_asof_ms:now}));
  const signals=[
    {id:18,symbol:"MCX",action:"ENTER_LONG",status:"ACCEPTED",reason:null,received_ms:now-60000,cycle_id:"demo:MCX:1",decision:{required_paise:6149520,available_paise:24593000},payload:{source:"Illustrative browser sandbox",symbol:"MCX",action:"ENTER_LONG"}},
    {id:17,symbol:"CANBK",action:"ENTER_LONG",status:"SKIPPED",reason:"INSUFFICIENT_FUNDS",received_ms:now-180000,decision:{required_paise:6000000,available_paise:1800000},payload:{source:"Illustrative browser sandbox"}},
    {id:16,symbol:"BEL",action:"ENTER_LONG",status:"FILLED",received_ms:now-720000,decision:{required_paise:6000000},payload:{source:"Illustrative browser sandbox"}},
    {id:15,symbol:"NTPC",action:"EXIT_LONG",status:"FILLED",received_ms:now-1200000,decision:{quantity:100},payload:{source:"Illustrative browser sandbox"}},
    {id:14,symbol:"SBIN",action:"ENTER_LONG",status:"SKIPPED",reason:"STALE_ENTRY",received_ms:now-1800000,decision:{required_paise:6000000},payload:{source:"Illustrative browser sandbox"}},
    {id:13,symbol:"POLYCAB",action:"ENTER_LONG",status:"SKIPPED",reason:"POSITION_OR_ENTRY_EXISTS",received_ms:now-2400000,decision:{required_paise:6000000},payload:{source:"Illustrative browser sandbox"}}
  ];
  return {
    portfolio:{mode:"paper",initial_paise:50000000,pool_paise:51246000,realized_net_paise:1246000,cash_paise:27193000,reserved_paise:6149520,unsettled_paise:2600000,available_paise:18443480,next_allocation_paise:6149520,equity_paise:51496800,valuation_complete_and_fresh:true,entries_paused:false,reconciliation_required:false,positions},
    orders:[{id:12,signal_id:18,cycle_id:"demo:MCX:1",symbol:"MCX",side:"BUY",status:"PENDING",quantity:0,filled_quantity:0,budget:6149520,reserved_left:6149520,created_ms:now-60000,reason:null},{id:11,symbol:"BEL",side:"BUY",status:"FILLED",quantity:152,filled_quantity:152,budget:6000000,reserved_left:0,created_ms:now-720000},{id:10,symbol:"NTPC",side:"SELL",status:"FILLED",quantity:100,filled_quantity:100,budget:0,reserved_left:0,created_ms:now-1200000}],
    fills:[{id:11,order_id:11,symbol:"BEL",side:"BUY",quantity:152,price:38758,fees:8837,realized:0,filled_ms:now-720000},{id:10,order_id:10,symbol:"NTPC",side:"SELL",quantity:100,price:40000,fees:8000,realized:1246000,filled_ms:now-1200000}],
    signals,skipped:signals.filter(s=>s.status==="SKIPPED"),
    trades:[{cycle_id:"demo:NTPC:closed",symbol:"NTPC",opened_ms:now-259200000,closed_ms:now-1200000,realized:1246000}],
    ledger:[{id:4,kind:"SELL_FILL",time_ms:now-1200000,cash_delta:2746000,reserve_delta:0,unsettled_delta:2600000,realized_delta:1246000,cash_after:27193000,reserved_after:0},{id:3,kind:"BUY_FILL",time_ms:now-720000,cash_delta:-5900000,reserve_delta:-5900000,unsettled_delta:0,realized_delta:0,cash_after:24447000,reserved_after:0},{id:2,kind:"RESERVE",time_ms:now-780000,cash_delta:0,reserve_delta:6000000,unsettled_delta:0,realized_delta:0,cash_after:30347000,reserved_after:6000000},{id:1,kind:"INITIAL_DEPOSIT",time_ms:now-604800000,cash_delta:50000000,reserve_delta:0,unsettled_delta:0,realized_delta:0,cash_after:50000000,reserved_after:0}],
    summary:{signals:18,skipped:3,active_orders:1,closed_trades:1},
    readiness:{ready_for_new_entries:false,checks:[{name:"environment",status:"INFO",message:"Illustrative browser sandbox. No real feed or account is connected."},{name:"allocation",status:"PASS",message:"12% of the example fund pool per new entry."},{name:"trade_matching",status:"PASS",message:"Example exits match their entry trade cycle."},{name:"broker_access",status:"WAITING",message:"Broker access is not needed to explore this demo."}],market_open:false}
  };
}
state.data=demoData();

function badge(value) {
  const good=["FILLED","PASS"], bad=["SKIPPED","BLOCKED","REJECTED"], waiting=["PENDING","PARTIALLY_FILLED","WAITING","PARTIAL","ACCEPTED","QUEUED"];
  const cls=good.includes(value)?"good":bad.includes(value)?"bad":waiting.includes(value)?"wait":"blue";
  return `<span class="badge ${cls}">${h(human(value))}</span>`;
}
function stock(symbol) {return `<div class="stock"><span class="stock-icon">${h(symbol.slice(0,2))}</span><span>${h(symbol)}<small>NSE · Equity</small></span></div>`;}
function empty(title,description) {return `<div class="empty-state"><div class="empty-icon">◫</div><h3>${h(title)}</h3><p>${h(description)}</p></div>`;}
function metric(label,value,caption,icon="◈",valueClass="") {return `<div class="metric"><div class="metric-top"><span>${h(label)}</span><span class="metric-icon">${icon}</span></div><div class="metric-value ${valueClass}">${value}</div><p class="metric-caption">${caption}</p></div>`;}
function metrics() {
  const p=state.data.portfolio;
  const growth=p.initial_paise?((p.realized_net_paise/p.initial_paise)*100).toFixed(2):"0.00";
  return `<div class="metrics">${metric("Fund pool",money(p.pool_paise),`<span class="${pnlClass(p.realized_net_paise)}">${p.realized_net_paise>0?"↗ +":""}${growth}%</span> from starting capital`,"◈")}${metric("Available to allocate",money(p.available_paise),"After reservations and settlement holds","▤")}${metric("Realized net P&L",signedMoney(p.realized_net_paise),`${h(state.data.summary.closed_trades)} closed trade cycles`,"↗",pnlClass(p.realized_net_paise))}${metric("Next trade budget",money(p.next_allocation_paise,2),"12% of current fund pool","◷")}</div>`;
}
function allocation() {
  const p=state.data.portfolio, invested=p.positions.reduce((sum,p)=>sum+p.cost_paise,0);
  const entries=[["Invested",invested,"invested","#4267df"],["Reserved",p.reserved_paise,"reserved","#9bb1f4"],["Unsettled",p.unsettled_paise,"unsettled","#e8bb70"],["Available",p.available_paise,"available","#6fc4ae"]];
  let offset=0;
  const total=Math.max(1,p.pool_paise), circumference=2*Math.PI*64;
  const circles=entries.map(([,value,,color])=>{const arc=Math.max(0,value)/total*circumference;const output=`<circle cx="83" cy="83" r="64" fill="none" stroke="${color}" stroke-width="13" stroke-dasharray="${arc} ${circumference}" stroke-dashoffset="${-offset}"/>`;offset+=arc;return output;}).join("");
  const deployed=(invested/total*100).toFixed(1);
  return `<article class="card"><div class="card-heading"><div><h2>Capital allocation</h2><p class="card-subtitle">Where your fund pool is right now</p></div><span class="badge blue">INR</span></div><div class="allocation-content"><div class="donut" role="img" aria-label="Capital allocation: ${entries.map(([name,value])=>h(name+" "+money(value))).join(", ")}"><svg viewBox="0 0 166 166" aria-hidden="true"><circle cx="83" cy="83" r="64" fill="none" stroke="#f0f3f8" stroke-width="13"/>${circles}</svg><div class="donut-center"><small>Capital invested</small><strong>${deployed}%</strong><span>of the fund pool</span></div></div><div><div class="allocation-key">${entries.map(([name,value,cls])=>`<div class="allocation-row"><span class="allocation-dot ${cls}"></span><span>${name}</span><strong>${money(value)}</strong></div>`).join("")}</div><div class="allocation-footer"><span>Allocation per signal</span><strong>12% of pool</strong></div></div></div></article>`;
}
function engineCard() {
  const p=state.data.portfolio, r=state.data.readiness;
  return `<article class="card"><div class="card-heading"><div><h2>Execution engine</h2><p class="card-subtitle">${state.source==="demo"?"Illustrative account rules":"Current paper-account state"}</p></div>${badge(p.entries_paused?"WAITING":state.source==="demo"?"INFO":r.ready_for_new_entries?"PASS":"WAITING")}</div><div class="engine-summary"><div class="engine-summary-row"><span>New entries</span><strong>${p.entries_paused?"Paused":"Enabled"}</strong></div><div class="engine-summary-row"><span>Active orders</span><strong>${state.data.summary.active_orders}</strong></div><div class="engine-summary-row"><span>Open positions</span><strong>${p.positions.length} stocks</strong></div><div class="engine-summary-row"><span>Entry priority</span><strong>First received · first processed</strong></div><p class="engine-hint">${state.source==="demo"?"Sandbox actions stay in your browser. Switch to your paper account to view the actual server ledger.":p.entries_paused?"Existing accepted orders and exits continue while new admissions are paused.":"Execution requires an open market, fresh quotes and available funds."}</p></div></article>`;
}
function positionsTable(limit=null) {
  const data=state.data.portfolio.positions.filter(p=>p.symbol.toLowerCase().includes(state.search.toLowerCase()));
  const rows=(limit?data.slice(0,limit):data).map(p=>`<tr><td>${stock(p.symbol)}</td><td class="table-number">${p.quantity}</td><td>${money(p.cost_paise,2)}</td><td>${money(p.market_value_paise,2)}${p.quote_stale?` <span class="badge wait">Stale mark</span>`:""}</td><td class="${pnlClass(p.unrealized_paise)}">${signedMoney(p.unrealized_paise,2)}</td><td><button class="table-action" data-close-position="${h(p.cycle_id)}">Request exit</button></td></tr>`).join("");
  return `<article class="card table-card"><div class="card-heading"><div><h2>Open positions <span class="badge blue">${state.data.portfolio.positions.length}</span></h2><p class="card-subtitle">${state.source==="demo"?"Example holdings · illustrative marks":"Actual paper holdings · quote freshness shown per stock"}</p></div>${limit?`<button class="text-button" data-view="positions">View all positions ↗</button>`:searchBox()}</div>${data.length?`<div class="table-wrap"><table><thead><tr><th>Stock</th><th>Quantity</th><th>Remaining cost</th><th>Market value</th><th>Unrealized P&L</th><th>Action</th></tr></thead><tbody>${rows}</tbody></table></div>`:empty("No open positions",state.search?"No stocks match this search.":"Positions will appear after a valid entry receives a paper fill. No sample trades are added to your account.")}</article>`;
}
function searchBox() {return `<div class="table-toolbar"><input class="search-input" id="table-search" type="search" placeholder="Search stocks…" aria-label="Search stocks" value="${h(state.search)}"></div>`;}
function recordFooter(count) {return `<div class="table-footer"><span>${count} loaded records · newest first${state.source==="demo"?" · illustrative":""}</span>${state.source==="account"&&count>=state.limit&&state.limit<500?`<button class="text-button" id="load-more">Show more</button>`:""}</div>`;}
function activity() {
  const signals=state.data.signals.slice(0,4);
  return `<article class="card"><div class="card-heading"><div><h2>Signal activity</h2><p class="card-subtitle">Latest received decisions</p></div><button class="text-button" data-view="signals">View inbox ↗</button></div>${signals.length?`<div class="activity-list">${signals.map(s=>`<div class="activity-item"><span class="activity-icon ${s.status==="SKIPPED"?"skipped":""}">${s.status==="SKIPPED"?"⊘":s.action==="ENTER_LONG"?"↗":"↙"}</span><div><strong>${h(s.symbol)} · ${s.action==="ENTER_LONG"?"Entry":"Exit"} ${h(human(s.status).toLowerCase())}</strong><p>${s.reason?h(human(s.reason)):s.status==="ACCEPTED"?"Funds reserved · awaiting execution":s.status==="FILLED"?"Paper execution complete":"Received for processing"}</p></div><time>${h(date(s.received_ms))}</time></div>`).join("")}</div>`:empty("Waiting for the first signal","Your TradingView alerts will appear here after the webhook is connected.")}</article>`;
}
function skippedSummary() {
  return `<article class="card"><div class="card-heading"><div><h2>Skipped signals</h2><p class="card-subtitle">Decisions you can account for</p></div><span class="badge wait">${state.data.summary.skipped}</span></div><div class="engine-summary"><div class="engine-summary-row"><span>Insufficient funds</span><strong>${state.data.skipped.filter(s=>s.reason==="INSUFFICIENT_FUNDS").length} loaded</strong></div><div class="engine-summary-row"><span>Other reasons</span><strong>${state.data.skipped.filter(s=>s.reason!=="INSUFFICIENT_FUNDS").length} loaded</strong></div><p class="engine-hint">Skipped entries are recorded permanently. Available funds later do not replay an old signal.</p><button class="text-button" data-view="skipped">Review skipped signals →</button></div></article>`;
}
function signalTable(skipped=false) {
  const source=skipped?state.data.skipped:state.data.signals;
  const data=source.filter(p=>p.symbol.toLowerCase().includes(state.search.toLowerCase()));
  const rows=data.map(s=>`<tr><td class="time-cell">#${s.id}<br>${h(date(s.received_ms))}</td><td>${stock(s.symbol)}</td><td>${badge(s.action==="ENTER_LONG"?"BUY":"EXIT")}</td><td>${badge(s.status)}</td><td class="reason">${h(human(s.reason))}</td><td>${money(s.decision?.required_paise)}</td><td>${money(s.decision?.available_paise)}</td><td><button class="table-action" data-signal-detail="${s.id}">Inspect</button></td></tr>`).join("");
  return `<article class="card table-card"><div class="card-heading"><div><h2>${skipped?"Skipped signal decisions":"Received signals"}</h2><p class="card-subtitle">${skipped?"Includes the fund snapshot at the decision":"IDs preserve receipt order; exits receive execution priority"}</p></div>${searchBox()}</div>${data.length?`<div class="table-wrap"><table><thead><tr><th>Receipt</th><th>Stock</th><th>Action</th><th>Outcome</th><th>Reason</th><th>Required</th><th>Available</th><th>Trace</th></tr></thead><tbody>${rows}</tbody></table></div>`:empty(skipped?"No skipped signals":"No signals received",state.search?"No stocks match this search.":skipped?"Every skipped signal will include its reason and allocation details.":"Connect TradingView when you are ready. This inbox does not generate trades on its own.")}${recordFooter(source.length)}</article>`;
}
function ordersTable() {
  const data=state.data.orders.filter(o=>o.symbol.toLowerCase().includes(state.search.toLowerCase()));
  return `<article class="card table-card"><div class="card-heading"><div><h2>Paper orders</h2><p class="card-subtitle">Accepted orders can remain pending until a valid quote arrives</p></div>${searchBox()}</div>${data.length?`<div class="table-wrap"><table><thead><tr><th>Order / time</th><th>Stock</th><th>Side</th><th>Filled / target</th><th>Reserved</th><th>Status</th><th>Action</th></tr></thead><tbody>${data.map(o=>`<tr><td class="time-cell">#${o.id}<br>${h(date(o.created_ms))}</td><td>${stock(o.symbol)}</td><td>${badge(o.side)}</td><td class="table-number">${o.filled_quantity} / ${o.quantity||"Sizing pending"}</td><td>${money(o.reserved_left,2)}</td><td>${badge(o.status)}${o.reason?`<div class="reason">${h(human(o.reason))}</div>`:""}</td><td>${o.side==="BUY"&&["PENDING","PARTIALLY_FILLED"].includes(o.status)?`<button class="table-action danger" data-cancel-order="${o.id}">Cancel remainder</button>`:"—"}</td></tr>`).join("")}</tbody></table></div>`:empty("No orders yet",state.search?"No stocks match this search.":"A validated entry or matching exit creates a paper order. Skipped entries create no orders.")}${recordFooter(state.data.orders.length)}</article>`;
}
function historyTable() {
  const data=state.data.trades.filter(t=>t.symbol.toLowerCase().includes(state.search.toLowerCase()));
  return `<article class="card table-card"><div class="card-heading"><div><h2>Closed trade cycles</h2><p class="card-subtitle">Net realized P&L includes the modeled entry and exit costs</p></div>${searchBox()}</div>${data.length?`<div class="table-wrap"><table><thead><tr><th>Stock</th><th>Opened</th><th>Closed</th><th>Net realized P&L</th></tr></thead><tbody>${data.map(t=>`<tr><td>${stock(t.symbol)}</td><td class="time-cell">${h(date(t.opened_ms))}</td><td class="time-cell">${h(date(t.closed_ms))}</td><td class="${pnlClass(t.realized)}">${signedMoney(t.realized,2)}</td></tr>`).join("")}</tbody></table></div>`:empty("No closed trades",state.search?"No stocks match this search.":"A trade cycle appears here once its remaining position is closed. Paper positions can stay open overnight.")}${recordFooter(state.data.trades.length)}</article>`;
}
function fillsTable() {
  const data=state.data.fills.filter(f=>f.symbol.toLowerCase().includes(state.search.toLowerCase()));
  return `<article class="card table-card"><div class="card-heading"><div><h2>Individual fills</h2><p class="card-subtitle">${state.source==="demo"?"Illustrative fill excerpts":"Each partial execution has its own quote and ledger movement"}</p></div>${searchBox()}</div>${data.length?`<div class="table-wrap"><table><thead><tr><th>Time / order</th><th>Stock</th><th>Side</th><th>Quantity</th><th>Price</th><th>Modeled charges</th><th>Realized P&L</th></tr></thead><tbody>${data.map(f=>`<tr><td class="time-cell">Order #${f.order_id}<br>${h(date(f.filled_ms))}</td><td>${stock(f.symbol)}</td><td>${badge(f.side)}</td><td class="table-number">${f.quantity}</td><td>${money(f.price,2)}</td><td>${money(f.fees,2)}</td><td class="${pnlClass(f.realized)}">${f.side==="SELL"?signedMoney(f.realized,2):"—"}</td></tr>`).join("")}</tbody></table></div>`:empty("No execution fills",state.search?"No stocks match this search.":"Orders require usable bid/ask quotes before the engine records a fill.")}${recordFooter(state.data.fills.length)}</article>`;
}
function ledgerTable() {
  const rows=state.data.ledger;
  return `<div class="notice">All figures are displayed in rupees. The engine stores integer paise. Reserved and unsettled funds are already part of cash; they are excluded only when calculating available funds.</div><article class="card table-card"><div class="card-heading"><div><h2>Fund movements</h2><p class="card-subtitle">${state.source==="demo"?"Illustrative excerpts, not a complete reconcilable ledger":"Append-only changes with balances after each movement"}</p></div></div>${rows.length?`<div class="table-wrap"><table><thead><tr><th>Time / sequence</th><th>Movement</th><th>Cash change</th><th>Reserve change</th><th>Unsettled change</th><th>Realized change</th><th>Cash after</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="time-cell">#${r.id}<br>${h(date(r.time_ms))}</td><td>${h(human(r.kind))}</td><td class="${pnlClass(r.cash_delta)}">${signedMoney(r.cash_delta,2)}</td><td>${signedMoney(r.reserve_delta,2)}</td><td>${signedMoney(r.unsettled_delta,2)}</td><td class="${pnlClass(r.realized_delta)}">${signedMoney(r.realized_delta,2)}</td><td class="table-number">${money(r.cash_after,2)}</td></tr>`).join("")}</tbody></table></div>`:empty("No ledger movements","The initial deposit and subsequent paper transactions will be recorded here.")}${recordFooter(rows.length)}</article>`;
}
function systemView() {
  const r=state.data.readiness;
  return `<div class="notice">${state.source==="demo"?"You are viewing example checks, not the running server. Switch to your paper account for actual health.":"Readiness verifies live-data paper execution. It does not prove TradingView delivery or guarantee that an individual entry has sufficient funds."}</div><div class="dashboard-grid"><article class="card"><div class="card-heading"><div><h2>Readiness checks</h2><p class="card-subtitle">${state.source==="demo"?"Sandbox environment":r.ready_for_new_entries?"Ready for live-data paper entries":"Some checks are blocked or waiting"}</p></div>${badge(r.ready_for_new_entries?"PASS":"WAITING")}</div><div class="health-list">${r.checks.map(c=>`<div class="health-row"><div><h3>${h(human(c.name))}</h3><p>${h(c.message)}</p></div>${badge(c.status)}</div>`).join("")}</div></article><article class="card"><div class="card-heading"><div><h2>Strategy configuration</h2><p class="card-subtitle">Agreed portfolio rules</p></div></div><div class="config-list">${[["Execution","Paper only"],["Stock universe","20 NSE equities"],["Signal timeframe","2 hours"],["Per-trade allocation","12% of realized pool"],["Position limit","One per stock"],["Overnight holding","Enabled"],["Automatic stop-loss","None"],["Exit rule","Matching sell signal"]].map(([key,value])=>`<div class="config-item"><span>${key}</span><strong>${value}</strong></div>`).join("")}</div></article></div>`;
}

function render() {
  const focusSearch=document.activeElement?.id==="table-search", caret=focusSearch?$("#table-search").selectionStart:0;
  const [title,heading,description]=views[state.view];
  $("#breadcrumb-current").textContent=title;$("#page-title").textContent=heading;$("#page-description").textContent=description;
  document.querySelectorAll(".nav-item").forEach(el=>{el.classList.toggle("active",el.dataset.view===state.view);if(el.dataset.view===state.view)el.setAttribute("aria-current","page");else el.removeAttribute("aria-current");});
  const p=state.data.portfolio;
  $("#position-count").textContent=p.positions.length;$("#skip-count").textContent=state.data.summary.skipped;
  $("#pause-button").textContent=p.entries_paused?"▶ Resume entries":"Ⅱ Pause entries";
  $("#source-select").value=state.source;
  $("#account-label").textContent=state.authenticated?"Sign out":"Connect paper account";
  $("#account-button").setAttribute("aria-label",state.authenticated?"Sign out of paper account":"Connect paper account");
  $("#source-banner").classList.toggle("account",state.source==="account");
  $("#source-banner div").innerHTML=state.source==="demo"?"<strong>Demo sandbox</strong><span>Illustrative data. Changes here stay in this browser and never touch your paper account.</span>":"<strong>Your paper account</strong><span>Actual server records. Orders are simulated; no live broker execution exists.</span>";
  $("#connect-inline").textContent=state.source==="demo"?"Open my paper account ↗":"View system health ↗";
  $("#connection-status").textContent=state.source==="demo"?"Illustrative data · browser sandbox":state.error?"Connection interrupted":"Connected to local paper server";
  $("#connection-dot").className="connection-dot"+(state.source==="account"?(state.error?" bad":" good"):"");
  $("#updated-at").textContent=state.lastUpdated?`Updated ${time(state.lastUpdated)} IST`:"Illustrative snapshot";
  $("#error-banner").hidden=!state.error;$("#error-banner").textContent=state.error;
  let content;
  switch(state.view){
    case "overview":content=metrics()+`<div class="dashboard-grid">${allocation()}${engineCard()}</div><div class="section-gap">${positionsTable(4)}</div><div class="bottom-grid">${activity()}${skippedSummary()}</div>`;break;
    case "positions":content=positionsTable();break;
    case "orders":content=ordersTable();break;
    case "fills":content=fillsTable();break;
    case "signals":content=signalTable();break;
    case "skipped":content=signalTable(true);break;
    case "history":content=historyTable();break;
    case "ledger":content=ledgerTable();break;
    case "system":content=systemView();break;
  }
  $("#content").innerHTML=content;
  if(focusSearch&&$("#table-search")){ $("#table-search").focus();$("#table-search").setSelectionRange(caret,caret); }
}
async function api(path,options={}) {
  const headers={...(options.body?{"Content-Type":"application/json"}:{}),...(state.csrf?{"X-CSRF-Token":state.csrf}:{}),...options.headers};
  const response=await fetch(path,{...options,headers,credentials:"same-origin",cache:"no-store"});
  if(response.status===401){state.authenticated=false;state.csrf=null;throw new Error("Your session expired. Reconnect your paper account.");}
  if(!response.ok){let detail="Request failed. Please retry.";try{detail=(await response.json()).detail||detail;}catch{}throw new Error(typeof detail==="string"?detail:"Request could not be completed.");}
  return response.json();
}
async function refresh() {
  if(state.loading)return;
  if(state.source==="demo"){state.lastUpdated=Date.now();state.error="";render();return;}
  state.loading=true;$("#refresh-button").disabled=true;
  try {
    const names=["portfolio","orders","fills","signals","skipped","trades","ledger","readiness","summary"];
    const results=await Promise.all(names.map(name=>api(`/api/${name}${["portfolio","readiness","summary"].includes(name)?"":`?limit=${state.limit}&order=desc`}`)));
    if(state.source!=="account")return;
    const data={};names.forEach((name,index)=>data[name==="trades"?"trades":name]=results[index].items||results[index]);
    state.data=data;state.error="";state.lastUpdated=Date.now();
  } catch(error) {if(state.source==="account")state.error=error.message+" Existing values may be out of date.";}
  finally{state.loading=false;$("#refresh-button").disabled=false;render();}
}
function showToast(message) {$("#toast").textContent=message;$("#toast").hidden=false;clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>$("#toast").hidden=true,5000);}
function navigate(view) {if(!views[view])return;state.view=view;state.search="";location.hash=view;$("#sidebar").classList.remove("open");$("#menu-toggle").setAttribute("aria-expanded","false");render();}
function ask(title,description,label) {const dialog=$("#confirm-dialog");dialog.querySelector(".eyebrow").textContent=state.source==="demo"?"DEMO SANDBOX CONTROL":"PAPER ACCOUNT CONTROL";$("#confirm-title").textContent=title;$("#confirm-description").textContent=(state.source==="demo"?"Demo only. ":"")+description;$("#confirm-submit").textContent=label;dialog.returnValue="";dialog.showModal();return new Promise(resolve=>dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true}));}
function connect() {if(state.authenticated){switchSource("account");return;}$("#login-error").textContent="";$("#login-dialog").showModal();}
async function switchSource(source) {
  if(source==="account"&&!state.authenticated){$("#source-select").value=state.source;connect();return;}
  if(source==="demo"){state.source="demo";state.data=demoData();state.error="";state.lastUpdated=Date.now();render();}
  else {
    // Do not display demo funds under an actual-account banner while loading.
    const previous=state.source;state.source="account";
    if(previous!=="account"){
      try {const p=await api("/api/portfolio");state.data={portfolio:p,orders:[],fills:[],signals:[],skipped:[],trades:[],ledger:[],summary:{signals:0,skipped:0,active_orders:0,closed_trades:0},readiness:{ready_for_new_entries:false,checks:[]}};}
      catch(error){state.source=previous;showToast(error.message);render();return;}
    }
    await refresh();
  }
}

$("#login-form").addEventListener("submit",async event=>{
  event.preventDefault();const input=$("#admin-token"),token=input.value;input.value="";$("#login-submit").disabled=true;
  try {const response=await fetch("/auth/session",{method:"POST",headers:{Authorization:"Bearer "+token},credentials:"same-origin",cache:"no-store"});if(!response.ok)throw new Error("Could not sign in. Check the admin token in your local .env file.");const session=await response.json();state.authenticated=true;state.csrf=session.csrf_token;$("#login-dialog").close();await switchSource("account");}
  catch(error){$("#login-error").textContent=error.message;}
  finally{$("#login-submit").disabled=false;}
});
$("#source-select").addEventListener("change",event=>switchSource(event.target.value));
$("#refresh-button").addEventListener("click",()=>refresh());
$("#connect-inline").addEventListener("click",()=>state.source==="demo"?connect():navigate("system"));
$("#account-button").addEventListener("click",async()=>{if(!state.authenticated){connect();return;}try{await api("/auth/session",{method:"DELETE"});state.authenticated=false;state.csrf=null;await switchSource("demo");showToast("Signed out of your paper account.");}catch(error){showToast(error.message);}});
$("#menu-toggle").addEventListener("click",()=>{$("#sidebar").classList.toggle("open");$("#menu-toggle").setAttribute("aria-expanded",$("#sidebar").classList.contains("open")?"true":"false");});
$("#pause-button").addEventListener("click",async()=>{
  const paused=!state.data.portfolio.entries_paused;
  if(!await ask(paused?"Pause new entries?":"Resume new entries?","Existing accepted orders and position exits stay active.",paused?"Pause entries":"Resume entries"))return;
  try{if(state.source==="demo")state.data.portfolio.entries_paused=paused;else await api("/controls/entries",{method:"POST",body:JSON.stringify({paused})});await refresh();showToast(state.source==="demo"?"Demo control updated. Your paper account is unchanged.":paused?"New entries paused.":"New entries resumed.");}catch(error){showToast(error.message);}
});
document.addEventListener("input",event=>{if(event.target.id==="table-search"){state.search=event.target.value;render();}});
document.addEventListener("click",async event=>{
  const target=event.target.closest("button,a");if(!target)return;
  if(target.dataset.view){navigate(target.dataset.view);return;}
  if(target.hasAttribute("data-close-dialog")){target.closest("dialog").close();return;}
  if(target.id==="load-more"){state.limit=Math.min(500,state.limit+100);await refresh();return;}
  if(target.dataset.signalDetail){try{const trace=state.source==="demo"?(state.data.signals.find(s=>String(s.id)===target.dataset.signalDetail)||state.data.skipped.find(s=>String(s.id)===target.dataset.signalDetail)):await api(`/api/signals/${Number(target.dataset.signalDetail)}/trace`);$("#detail-content").textContent=JSON.stringify(trace,null,2);$("#detail-dialog").showModal();}catch(error){showToast(error.message);}return;}
  if(target.dataset.cancelOrder){
    const id=Number(target.dataset.cancelOrder);if(!await ask("Cancel remaining entry?","Already filled shares remain in their position. The unspent reservation will be released.","Cancel remainder"))return;
    try{if(state.source==="demo"){const order=state.data.orders.find(o=>o.id===id);state.data.portfolio.reserved_paise-=order.reserved_left;state.data.portfolio.available_paise+=order.reserved_left;order.reserved_left=0;order.status="CANCELLED";state.data.summary.active_orders-=1;const signal=state.data.signals.find(s=>s.id===order.signal_id);if(signal){signal.status="CANCELLED";signal.reason="MANUAL_CANCEL";}}else await api(`/controls/orders/${id}/cancel`,{method:"POST"});await refresh();showToast(state.source==="demo"?"Demo order cancelled. No server data changed.":"Remaining entry cancelled.");}catch(error){showToast(error.message);}return;
  }
  if(target.dataset.closePosition){
    const cycle=target.dataset.closePosition;
    if(!await ask("Request a paper exit?","This queues an exit for the remaining shares. Execution still needs an open market and a fresh quote.","Request paper exit"))return;
    try{if(state.source==="demo"){if(!state.data.orders.some(o=>o.cycle_id===cycle&&o.side==="SELL"&&o.status==="PENDING")){const p=state.data.portfolio.positions.find(p=>p.cycle_id===cycle);state.data.orders.unshift({id:100+state.data.orders.length,cycle_id:cycle,symbol:p.symbol,side:"SELL",status:"PENDING",quantity:p.quantity,filled_quantity:0,reserved_left:0,created_ms:Date.now()});state.data.summary.active_orders+=1;}}else await api("/controls/positions/close",{method:"POST",body:JSON.stringify({cycle_id:cycle})});await refresh();showToast(state.source==="demo"?"Demo exit queued. No actual position changed.":"Paper exit queued; awaiting execution.");}catch(error){showToast(error.message);}
  }
});
window.addEventListener("hashchange",()=>{const view=location.hash.slice(1);if(views[view]&&view!==state.view)navigate(view);});
async function init(){state.view=views[location.hash.slice(1)]?location.hash.slice(1):"overview";render();try{const response=await fetch("/auth/session",{credentials:"same-origin",cache:"no-store"});if(response.ok){const session=await response.json();if(session.authenticated){state.authenticated=true;state.csrf=session.csrf_token;await switchSource("account");}}}catch{}setInterval(()=>{if(state.source==="account"&&!document.hidden&&!document.querySelector("dialog[open]"))refresh();},5000);}
init();
