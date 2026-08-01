"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchBtcStraddleState, fetchBtcStraddlePositions, fetchBtcStraddleEquityHistory, fetchBtcPrice, fetchTyagachState, fetchTyagachPositions, fetchTyagachEquityHistory, fetchTyagachChart, fetchJonyState, fetchJonyParams, fetchJonyPositions, fetchJonyEquityHistory, fetchJonyChart, type EquityPoint, type BtcStraddleState, type BtcStraddlePosition, type Kline, type TyagachState, type TyagachPosition, type TyagachChartZone, type JonyState, type JonyParams, type JonyPosition, type JonyChartData } from "./lib/api";
import MissionControl from "./components/MissionControl";
import StraddleChart from "./components/StraddleChart";
import TyagachChart from "./components/TyagachChart";
import JonyChart from "./components/JonyChart";
import { ActiveContractsRail, ItmBadge, Countdown, useLiveNow, type Contract } from "./components/ActiveContracts";

const REFRESH_MS = 15_000;

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtPct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const fmtTime = (ms: number) => {
  const d = new Date(ms);
  const now = Date.now();
  const diff = now - ms;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString();
};
const fmtRemaining = (hold_h: number, opened_ms: number) => {
  const elapsed_h = (Date.now() - opened_ms) / 3600000;
  const remaining = hold_h - elapsed_h;
  if (remaining <= 0) return "closing soon";
  if (remaining < 24) return `${remaining.toFixed(1)}h left`;
  return `${(remaining / 24).toFixed(1)}d left`;
};
const fmtDay = (ms: number) => {
  const d = new Date(ms);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${days[d.getDay()]} ${d.getDate()}`;
};
export default function Dashboard() {
  const now = useLiveNow(1000);

  const [btcState, setBtcState] = useState<BtcStraddleState | null>(null);
  const [btcPositions, setBtcPositions] = useState<BtcStraddlePosition[]>([]);
  const [btcRecentTrades, setBtcRecentTrades] = useState<BtcStraddlePosition[]>([]);
  const [btcEquityHistory, setBtcEquityHistory] = useState<EquityPoint[]>([]);
  const [btcError, setBtcError] = useState<string | null>(null);
  const [btcSpot, setBtcSpot] = useState<number | null>(null);

  const [tyagachState, setTyagachState] = useState<TyagachState | null>(null);
  const [tyagachOpenPositions, setTyagachOpenPositions] = useState<TyagachPosition[]>([]);
  const [tyagachRecentTrades, setTyagachRecentTrades] = useState<TyagachPosition[]>([]);
  const [tyagachEquityHistory, setTyagachEquityHistory] = useState<EquityPoint[]>([]);
  const [tyagachError, setTyagachError] = useState<string | null>(null);
  const [tyagachKlines, setTyagachKlines] = useState<Kline[]>([]);
  const [tyagachZones, setTyagachZones] = useState<TyagachChartZone[]>([]);

  const [jonyState, setJonyState] = useState<JonyState | null>(null);
  const [jonyParams, setJonyParams] = useState<JonyParams | null>(null);
  const [jonyOpenPositions, setJonyOpenPositions] = useState<JonyPosition[]>([]);
  const [jonyRecentTrades, setJonyRecentTrades] = useState<JonyPosition[]>([]);
  const [jonyEquityHistory, setJonyEquityHistory] = useState<EquityPoint[]>([]);
  const [jonyChart, setJonyChart] = useState<JonyChartData | null>(null);
  const [jonyError, setJonyError] = useState<string | null>(null);

  // Own effect/error state per bot — each is a distinct deploy (own
  // container/tables or own service) and may lag behind or be absent; one
  // bot's fetch failure must never blank out the rest of the dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, p, t, eq, priceRes] = await Promise.all([
          fetchBtcStraddleState(),
          fetchBtcStraddlePositions("open"),
          fetchBtcStraddlePositions("recent", 200),
          fetchBtcStraddleEquityHistory(336),
          fetchBtcPrice().catch(() => null),
        ]);
        if (cancelled) return;
        setBtcState(s);
        setBtcPositions(p.positions);
        setBtcRecentTrades(t.positions.filter((pos) => pos.closed_at_ms !== null));
        setBtcEquityHistory(eq.points);
        setBtcSpot(priceRes?.price ?? null);
        setBtcError(null);
      } catch (e) {
        if (cancelled) return;
        setBtcError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Tyagach is a fully separate SERVICE, not just a separate container in
  // this same opt-app deploy — own repo (TG), own SQLite, own API on :8100,
  // no opt-app auth on that call path (see lib/api.ts's TYAGACH_API_BASE
  // comment). Isolated the same way as the BTC/ETH straddle effects above:
  // its unreachability must never blank out the rest of the dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, op, rt, eq, chart] = await Promise.all([
          fetchTyagachState(),
          fetchTyagachPositions("open"),
          fetchTyagachPositions("closed", 200),
          fetchTyagachEquityHistory(2000),
          fetchTyagachChart(),
        ]);
        if (cancelled) return;
        setTyagachState(s);
        setTyagachOpenPositions(op);
        setTyagachRecentTrades(rt);
        setTyagachEquityHistory(eq);
        setTyagachKlines(chart.klines);
        setTyagachZones(chart.zones);
        setTyagachError(null);
      } catch (e) {
        if (cancelled) return;
        setTyagachError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Jony — same fully-separate-service pattern as Tyagach (own repo, own
  // SQLite, API on :8200); isolated effect so its unreachability never
  // blanks out the rest of the dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, prm, pos, eq, chart] = await Promise.all([
          fetchJonyState(),
          fetchJonyParams(),
          fetchJonyPositions(200),
          fetchJonyEquityHistory(2000),
          fetchJonyChart(),
        ]);
        if (cancelled) return;
        setJonyState(s);
        setJonyParams(prm);
        setJonyOpenPositions(pos.open);
        setJonyRecentTrades(pos.recent.filter((p) => p.status !== "open"));
        setJonyEquityHistory(eq);
        setJonyChart(chart);
        setJonyError(null);
      } catch (e) {
        if (cancelled) return;
        setJonyError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Unified, live-spot-aware view of every open short-option position across
  // the fleet — feeds the global "Active Contracts" rail/drawer. Built with
  // useMemo so the 1s countdown ticker (inside ActiveContractsRail) doesn't
  // force this mapping to rerun every second, only when positions/spots change.
  const allContracts: Contract[] = useMemo(() => [
    ...btcPositions.map((p): Contract => ({
      key: `boba1-${p.id}`, bot: "btc_straddle", side: p.leg, strike: p.strike,
      expiryMs: p.expiry_ms, contracts: p.contracts, spot: btcSpot,
      entryCreditUsd: p.entry_credit_usd, openedAtMs: p.opened_at_ms, cycleId: p.cycle_id,
      currentMarkUsd: p.current_mark_usd, unrealizedPnlUsd: p.unrealized_pnl_usd,
    })),
    ...tyagachOpenPositions.map((p): Contract => ({
      key: `tyagach-${p.id}`, bot: "tyagach", side: p.option_side, strike: p.strike,
      expiryMs: p.expiry_ts_ms, contracts: p.num_units, spot: tyagachKlines.at(-1)?.close ?? null,
      entryCreditUsd: p.sell_premium_received, openedAtMs: p.entry_ts_ms,
      currentMarkUsd: p.current_mark_usd, unrealizedPnlUsd: p.unrealized_pnl_usd,
    })),
    // Jony trades two underlyings from one book — spot must be per-position
    // (ETH from Tyagach's own klines feed, BTC from the straddle feed — Jony
    // has no live-price feed of its own, it borrows from siblings that do).
    ...jonyOpenPositions.map((p): Contract => ({
      key: `jony-${p.id}`, bot: "jony", side: p.side, strike: p.strike,
      expiryMs: p.expiry_ms, contracts: p.qty,
      spot: p.coin === "BTC" ? btcSpot : (tyagachKlines.at(-1)?.close ?? null),
      entryCreditUsd: p.entry_credit * p.qty, openedAtMs: p.opened_at_ms,
      currentMarkUsd: p.current_mark_usd, unrealizedPnlUsd: p.unrealized_pnl_usd,
    })),
  ], [btcPositions, tyagachOpenPositions, jonyOpenPositions, tyagachKlines, btcSpot]);

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">Options Fleet</h1>
            <p className="text-xs text-slate-500">Boba1 · Tyagach · Jony — paper</p>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        <ActiveContractsRail contracts={allContracts} now={now} />
        <MissionControl />

        {/* ───────────────────── BTC Straddle (separate book) ───────────────────── */}
        <div className="pt-2">
          <h2 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-3">
            BTC Straddle <span className="text-slate-600 font-normal">· 24h unconditional short ATM</span>
          </h2>
        </div>

        {btcError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            BTC straddle bot unreachable: {btcError}
          </div>
        )}

        {btcState && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(btcState.current_equity_usd)}
                sub={`${(btcState.current_equity_usd - btcState.start_equity_usd) >= 0 ? "+" : ""}${fmtUsd(btcState.current_equity_usd - btcState.start_equity_usd)}`}
                accent={btcState.current_equity_usd >= btcState.start_equity_usd ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard label="Win Rate" value={btcState.win_rate ? `${(btcState.win_rate * 100).toFixed(0)}%` : "—"} sub={`${btcState.wins}W / ${btcState.losses}L`} />
              <StatCard label="Legs closed" value={`${btcState.n_closed}`} sub={`${btcState.n_open} open`} />
              <StatCard label="Max DD" value={`${btcState.max_dd_pct.toFixed(1)}%`} sub={`cycle #${btcState.last_cycle_id}`} />
            </div>

            {btcEquityHistory.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Equity (14 days)
                </div>
                <div className="p-2">
                  <EquityChart points={btcEquityHistory} startEquity={btcState.start_equity_usd} />
                </div>
              </div>
            )}

            {btcPositions.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Open Legs ({btcPositions.length})
                </div>
                <div className="divide-y divide-slate-800">
                  {btcPositions.map((p) => (
                    <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                        }`}>
                          SELL {p.leg}
                        </span>
                        <span className="text-sm font-mono">${p.strike}</span>
                        <span className="text-xs text-slate-500">{p.contracts.toFixed(4)} BTC</span>
                        <ItmBadge side={p.leg} strike={p.strike} spot={btcSpot} compact />
                      </div>
                      <div className="text-right">
                        <Countdown expiryMs={p.expiry_ms} now={now} />
                        <p className="text-[10px] text-slate-600 mt-0.5">cycle #{p.cycle_id}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {btcRecentTrades.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал циклов</span>
                  <span>{btcRecentTrades.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {btcRecentTrades.map((t) => {
                    const isWin = (t.pnl_usd || 0) > 0;
                    return (
                      <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {t.leg}
                          </span>
                          <span className="font-mono text-xs">${t.strike}</span>
                          <span className="text-xs text-slate-500">{t.closed_at_ms ? fmtDay(t.closed_at_ms) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtPct(t.pnl_pct || 0)}
                          </span>
                          <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_usd != null ? fmtUsd(t.pnl_usd) : ""}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {btcPositions.length === 0 && btcRecentTrades.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Next cycle opens at the 24h boundary...</p>
              </div>
            )}
          </>
        )}

        {/* ───────────────────── Tyagach (separate service, own API) ───────────────────── */}
        <div className="pt-2">
          <h2 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-3">
            Tyagach <span className="text-slate-600 font-normal">· ETH OB/BB/MB zones · sell rich IV (paper)</span>
          </h2>
        </div>

        {tyagachError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            Tyagach unreachable: {tyagachError}
          </div>
        )}

        {tyagachState && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(tyagachState.equity_usd ?? tyagachState.balance_usdt ?? 0)}
                sub={`${((tyagachState.equity_usd ?? tyagachState.balance_usdt ?? 0) - tyagachState.start_balance_usdt) >= 0 ? "+" : ""}${fmtUsd((tyagachState.equity_usd ?? tyagachState.balance_usdt ?? 0) - tyagachState.start_balance_usdt)}${(tyagachState.unrealized_usd ?? 0) !== 0 ? ` · нереал ${(tyagachState.unrealized_usd ?? 0) >= 0 ? "+" : ""}${fmtUsd(tyagachState.unrealized_usd ?? 0)}` : ""}`}
                accent={(tyagachState.equity_usd ?? tyagachState.balance_usdt ?? 0) >= tyagachState.start_balance_usdt ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard
                label="Win Rate"
                value={tyagachState.win_rate != null ? `${(tyagachState.win_rate * 100).toFixed(0)}%` : "—"}
                sub={`${tyagachState.wins}W / ${tyagachState.losses}L`}
              />
              <StatCard
                label="Trades closed"
                value={`${tyagachState.n_closed}`}
                sub={`${tyagachState.open_position_count} open`}
              />
              <StatCard
                label="Max DD"
                value={`${tyagachState.max_dd_pct.toFixed(1)}%`}
                sub={tyagachState.paused ? "PAUSED" : "armed"}
              />
            </div>

            {tyagachEquityHistory.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Equity (paper)
                </div>
                <div className="p-2">
                  <EquityChart points={tyagachEquityHistory} startEquity={tyagachState.start_balance_usdt} />
                </div>
              </div>
            )}

            {tyagachOpenPositions.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Open positions ({tyagachOpenPositions.length})
                </div>
                <div className="divide-y divide-slate-800">
                  {tyagachOpenPositions.map((p) => (
                    <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.option_side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                        }`}>
                          SELL {p.option_side === "P" ? "PUT" : "CALL"}
                        </span>
                        <span className="text-xs text-slate-500">{p.zone_kind}</span>
                        <span className="text-sm font-mono">${p.strike}</span>
                        <span className="text-xs text-slate-500">{p.num_units.toFixed(2)} ETH</span>
                      </div>
                      <div className="text-right">
                        <Countdown expiryMs={p.expiry_ts_ms} now={now} />
                        <p className="text-[10px] text-slate-600 mt-0.5">{p.symbol}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {tyagachKlines.length > 1 && (
              <TyagachChart klines={tyagachKlines} zones={tyagachZones} />
            )}

            {tyagachRecentTrades.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал сделок</span>
                  <span>{tyagachRecentTrades.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {tyagachRecentTrades.map((t) => {
                    const isWin = (t.pnl_net || 0) > 0;
                    const pnlPct = t.sell_premium_received > 0 ? ((t.pnl_net || 0) / t.sell_premium_received) * 100 : 0;
                    return (
                      <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.option_side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {t.zone_kind} {t.option_side}
                          </span>
                          <span className="font-mono text-xs">${t.strike}</span>
                          <span className="text-xs text-slate-500">{t.exit_ts_ms ? fmtDay(t.exit_ts_ms) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtPct(pnlPct)}
                          </span>
                          <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_net != null ? fmtUsd(t.pnl_net) : ""}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {tyagachOpenPositions.length === 0 && tyagachRecentTrades.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Waiting for a zone signal with rich enough IV...</p>
              </div>
            )}
          </>
        )}

        {/* ───────────────────── Jony (separate service, own API :8200) ───────────────────── */}
        <div className="pt-2">
          <h2 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-3">
            Jony <span className="text-slate-600 font-normal">· ETH+BTC VRP basket · sell premium (paper)</span>
          </h2>
        </div>

        {jonyError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            Jony unreachable: {jonyError}
          </div>
        )}

        {jonyState && jonyState.initialized && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(jonyState.equity_usd)}
                sub={`${jonyState.equity_usd - jonyState.start_equity_usd >= 0 ? "+" : ""}${fmtUsd(jonyState.equity_usd - jonyState.start_equity_usd)} · start ${fmtUsd(jonyState.start_equity_usd, 0)}`}
                accent={jonyState.equity_usd >= jonyState.start_equity_usd ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard
                label="Win Rate"
                value={jonyState.win_rate != null ? `${(jonyState.win_rate * 100).toFixed(0)}%` : "—"}
                sub={`${jonyState.wins}W / ${jonyState.losses}L`}
              />
              <StatCard
                label="Trades closed"
                value={`${jonyState.n_closed}`}
                sub={`${jonyState.open_position_count} open`}
              />
              <StatCard
                label="Max DD"
                value={`${jonyState.max_dd_pct.toFixed(1)}%`}
                sub={
                  jonyState.paused
                    ? "PAUSED"
                    : now < jonyState.cb_cooldown_until_ms
                      ? `CB until ${new Date(jonyState.cb_cooldown_until_ms).toLocaleTimeString()}`
                      : "armed"
                }
                accent={jonyState.paused || now < jonyState.cb_cooldown_until_ms ? "text-amber-300" : undefined}
              />
            </div>

            {jonyParams && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Параметры (backtest-locked)</span>
                  <span className="text-slate-600">{jonyParams.backtest.finding}</span>
                </div>
                <div className="px-4 py-3 grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-[11px] font-mono text-slate-400">
                  <span>ETH: {jonyParams.coins.ETH?.join("+")} · BTC: {jonyParams.coins.BTC?.join("")}-only</span>
                  <span>PUT: vol≥{jonyParams.put_gen.vol_threshold} · {jonyParams.put_gen.regime_filter.join("/")} · MTF {jonyParams.put_gen.mtf_direction_filter}</span>
                  <span>CALL: vol≥{jonyParams.call_gen.vol_threshold} · {jonyParams.call_gen.regime_filter.join("/")} · 1h {jonyParams.call_gen.mtf_direction_filter} · bull≤{jonyParams.call_gen.bull_market_ratio_max}</span>
                  <span>PUT exit: TP2 {(jonyParams.put_exit.tp2_pct * 100).toFixed(0)}% · SL {(jonyParams.put_exit.sl_pct * 100).toFixed(0)}% · {jonyParams.put_exit.hold_h}h</span>
                  <span>CALL exit: TP2 {(jonyParams.call_exit.tp2_pct * 100).toFixed(0)}% · SL {(jonyParams.call_exit.sl_pct * 100).toFixed(0)}% · {jonyParams.call_exit.hold_h}h</span>
                  <span>MAX_OPEN {jonyParams.account.max_open_positions} · cap {jonyParams.account.per_coin_cap}/coin · margin {(jonyParams.account.margin_pct_per_trade * 100).toFixed(0)}% · CB {jonyParams.account.cb_consec_limit}→{jonyParams.account.cb_pause_hours}h · cooldown {jonyParams.account.cooldown_min}m</span>
                </div>
                <div className="px-4 pb-3 text-[10px] text-slate-600">
                  Бэктест 400д: +{jonyParams.backtest.full_return_pct}% · maxDD {jonyParams.backtest.max_dd_pct}% · holdout +{jonyParams.backtest.holdout_return_pct}% · ~{jonyParams.backtest.trades_per_day}/день
                </div>
              </div>
            )}

            {jonyEquityHistory.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Equity (paper, realized + mark-to-market)
                </div>
                <div className="p-2">
                  <EquityChart points={jonyEquityHistory} startEquity={jonyState.start_equity_usd} />
                </div>
              </div>
            )}

            {jonyChart && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {(["ETH", "BTC"] as const).map((coin) =>
                  (jonyChart.coins[coin]?.klines?.length ?? 0) > 1 ? (
                    <JonyChart
                      key={coin}
                      coin={coin}
                      klines={jonyChart.coins[coin].klines}
                      positions={jonyOpenPositions.filter((p) => p.coin === coin)}
                    />
                  ) : null,
                )}
              </div>
            )}

            {jonyOpenPositions.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Open positions ({jonyOpenPositions.length})
                </div>
                <div className="divide-y divide-slate-800">
                  {jonyOpenPositions.map((p) => (
                    <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.coin === "BTC" ? "bg-orange-500/10 text-orange-300" : "bg-sky-500/10 text-sky-300"
                        }`}>
                          {p.coin}
                        </span>
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                        }`}>
                          SELL {p.side === "P" ? "PUT" : "CALL"}
                        </span>
                        <span className="text-sm font-mono">${p.strike}</span>
                        <span className="text-xs text-slate-500">{p.qty.toFixed(2)} ct</span>
                        <span className="text-xs text-slate-500">credit ${(p.entry_credit * p.qty).toFixed(2)}</span>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-slate-400">{fmtRemaining(p.hold_h, p.opened_at_ms)}</p>
                        <p className="text-[10px] text-slate-600 mt-0.5">{p.option_symbol} · TP2 {(p.tp2_pct * 100).toFixed(0)}% / SL {(p.sl_pct * 100).toFixed(0)}%</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {jonyRecentTrades.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал сделок</span>
                  <span>{jonyRecentTrades.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {jonyRecentTrades.map((t) => {
                    const isWin = (t.pnl_usd || 0) > 0;
                    return (
                      <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {t.coin} {t.side}
                          </span>
                          <span className="font-mono text-xs">${t.strike}</span>
                          <span className="text-xs text-slate-500">{t.closed_at_ms ? fmtDay(t.closed_at_ms) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_pct != null ? fmtPct(t.pnl_pct) : ""}
                          </span>
                          <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_usd != null ? fmtUsd(t.pnl_usd) : ""}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {jonyOpenPositions.length === 0 && jonyRecentTrades.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Waiting for vol≥gate + regime + MTF to hold 4 of 5 minutes...</p>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}

function StatCard({ label, value, sub, accent }: { label: string; value: React.ReactNode; sub?: string; accent?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className={`text-xl font-bold font-mono mt-1 ${accent ?? "text-slate-100"}`}>{value}</p>
      {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function EquityChart({ points, startEquity }: { points: EquityPoint[]; startEquity: number }) {
  if (points.length < 2) return null;

  const w = 800, h = 120, pad = 4;
  const minEq = Math.min(...points.map(p => p.equity), startEquity);
  const maxEq = Math.max(...points.map(p => p.equity), startEquity);
  const range = maxEq - minEq || 1;

  const toX = (i: number) => pad + (i / (points.length - 1)) * (w - pad * 2);
  const toY = (v: number) => h - pad - ((v - minEq) / range) * (h - pad * 2);

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${toX(i)} ${toY(p.equity)}`).join(" ");
  const areaPath = linePath + ` L ${toX(points.length - 1)} ${h} L ${toX(0)} ${h} Z`;

  const isProfit = points[points.length - 1].equity >= startEquity;
  const lineColor = isProfit ? "#10b981" : "#f43f5e";
  const fillColor = isProfit ? "rgba(16,185,129,0.1)" : "rgba(244,63,94,0.1)";

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-28" preserveAspectRatio="none">
      {/* Start line */}
      <line x1={toX(0)} y1={toY(startEquity)} x2={toX(points.length - 1)} y2={toY(startEquity)} stroke="#334155" strokeWidth="1" strokeDasharray="4 4" />
      {/* Area */}
      <path d={areaPath} fill={fillColor} />
      {/* Line */}
      <path d={linePath} fill="none" stroke={lineColor} strokeWidth="2" />
      {/* Current value */}
      <text x={toX(points.length - 1)} y={toY(points[points.length - 1].equity) - 6} fill={lineColor} fontSize="11" fontWeight="bold" textAnchor="end">
        {fmtUsd(points[points.length - 1].equity)}
      </text>
    </svg>
  );
}
