"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchBtcPrice, fetchJonyState, fetchJonyParams, fetchJonyPositions, fetchJonyEquityHistory, fetchJonyChart, fetchJonyProximity, closeJonyPosition, fetchBubuState, fetchBubuCycles, fetchBubuEquityHistory, fetchBubuChart, pauseBubu, resumeBubu, closeBubuPosition, type EquityPoint, type Kline, type JonyState, type JonyParams, type JonyPosition, type JonyChartData, type JonyProximity, type BubuState, type BubuCycle, type BubuChartOverlay } from "./lib/api";
import MissionControl from "./components/MissionControl";
import StraddleChart from "./components/StraddleChart";
import JonyChart from "./components/JonyChart";
import BubuChart from "./components/BubuChart";
import BubuLadder from "./components/BubuLadder";
import EquityChart from "./components/EquityChart";
import ProximityGauge from "./components/ProximityGauge";
import { ActiveContractsRail, Countdown, useLiveNow, type Contract } from "./components/ActiveContracts";
import BubuSummaryRail from "./components/BubuSummary";

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

  // Boba1 (btc_straddle) archived 2026-08-04 — btcSpot kept, it still feeds
  // Jony's BTC-leg spot lookup below (allContracts + ItmBadge).
  const [btcSpot, setBtcSpot] = useState<number | null>(null);


  const [jonyState, setJonyState] = useState<JonyState | null>(null);
  const [jonyParams, setJonyParams] = useState<JonyParams | null>(null);
  const [jonyOpenPositions, setJonyOpenPositions] = useState<JonyPosition[]>([]);
  const [jonyRecentTrades, setJonyRecentTrades] = useState<JonyPosition[]>([]);
  const [jonyEquityHistory, setJonyEquityHistory] = useState<EquityPoint[]>([]);
  const [jonyChart, setJonyChart] = useState<JonyChartData | null>(null);
  const [jonyProximity, setJonyProximity] = useState<Record<"ETH" | "BTC", JonyProximity> | null>(null);
  const [jonyError, setJonyError] = useState<string | null>(null);
  const [closingJonyIds, setClosingJonyIds] = useState<Set<number>>(new Set());

  const [bubuState, setBubuState] = useState<BubuState | null>(null);
  const [bubuRecentCycles, setBubuRecentCycles] = useState<BubuCycle[]>([]);
  const [bubuEquityHistory, setBubuEquityHistory] = useState<EquityPoint[]>([]);
  const [bubuKlines, setBubuKlines] = useState<Kline[]>([]);
  const [bubuOverlay, setBubuOverlay] = useState<BubuChartOverlay | null>(null);
  const [bubuError, setBubuError] = useState<string | null>(null);
  const [bubuBusy, setBubuBusy] = useState(false);

  // BTC spot only (Boba1's own state/positions/equity fetch removed with the
  // archive) — Jony's BTC leg still needs a live spot for its ItmBadge/mark
  // display, isolated the same way every other bot's fetch is: a failure
  // here just leaves that badge blank, never blocks the rest of the dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const priceRes = await fetchBtcPrice().catch(() => null);
      if (cancelled) return;
      setBtcSpot(priceRes?.price ?? null);
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Jony — fully-separate-service pattern (own repo, own
  // SQLite, API on :8200); isolated effect so its unreachability never
  // blanks out the rest of the dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, prm, pos, eq, chart, prox] = await Promise.all([
          fetchJonyState(),
          fetchJonyParams(),
          fetchJonyPositions(200),
          fetchJonyEquityHistory(2000),
          fetchJonyChart(),
          // Isolated: an older deployed Jony without this route (or any
          // transient failure) must not blank the rest of the section —
          // same convention as fetchBtcPrice's isolation above.
          fetchJonyProximity().catch(() => null),
        ]);
        if (cancelled) return;
        setJonyState(s);
        setJonyParams(prm);
        setJonyOpenPositions(pos.open);
        setJonyRecentTrades(pos.recent.filter((p) => p.status !== "open"));
        setJonyEquityHistory(eq);
        setJonyChart(chart);
        setJonyProximity(prox);
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

  // Partial close (one position, not the whole book) — the loop executes on
  // its next ~5s tick same as Close All, so re-fetch positions right after
  // rather than waiting the full 15s poll for the row to disappear.
  const closeOneJonyPosition = async (id: number) => {
    setClosingJonyIds((prev) => new Set(prev).add(id));
    try {
      await closeJonyPosition(id);
      const pos = await fetchJonyPositions(200);
      setJonyOpenPositions(pos.open);
      setJonyRecentTrades(pos.recent.filter((p) => p.status !== "open"));
    } catch (e) {
      setJonyError(e instanceof Error ? e.message : String(e));
    } finally {
      setClosingJonyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  // BUBU — same fully-separate-service pattern as Jony (own repo,
  // own SQLite, API on :8300); isolated effect so its unreachability never
  // blanks out the rest of the dashboard. v1 baseline strategy, $300 start.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, cycles, eq, chart] = await Promise.all([
          fetchBubuState(),
          fetchBubuCycles(null, 200),
          fetchBubuEquityHistory(2000),
          fetchBubuChart(),
        ]);
        if (cancelled) return;
        setBubuState(s);
        setBubuRecentCycles(cycles.filter((c) => c.status === "closed"));
        setBubuEquityHistory(eq);
        setBubuKlines(chart.klines);
        setBubuOverlay(chart.overlay);
        setBubuError(null);
      } catch (e) {
        if (cancelled) return;
        setBubuError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const toggleBubuPause = async () => {
    if (!bubuState) return;
    setBubuBusy(true);
    try {
      if (bubuState.paused) await resumeBubu(); else await pauseBubu();
      setBubuState(await fetchBubuState());
    } catch (e) {
      setBubuError(e instanceof Error ? e.message : String(e));
    } finally {
      setBubuBusy(false);
    }
  };

  // Единственный открытый цикл (BUBU держит максимум один), тот же
  // single-writer/~POLL_SECONDS-tick паттерн, что и close-по-ID у флота —
  // не паузит бота, просто закрывает текущую позицию на следующем тике.
  const closeBubuOpenPosition = async () => {
    setBubuBusy(true);
    try {
      await closeBubuPosition();
      setBubuState(await fetchBubuState());
    } catch (e) {
      setBubuError(e instanceof Error ? e.message : String(e));
    } finally {
      setBubuBusy(false);
    }
  };

  // Unified, live-spot-aware view of every open short-option position across
  // the fleet — feeds the global "Active Contracts" rail/drawer. Built with
  // useMemo so the 1s countdown ticker (inside ActiveContractsRail) doesn't
  // force this mapping to rerun every second, only when positions/spots change.
  const allContracts: Contract[] = useMemo(() => [
    // Jony trades two underlyings from one book — spot must be per-position
    // (ETH from Jony's own chart klines, BTC from the straddle feed).
    ...jonyOpenPositions.map((p): Contract => ({
      key: `jony-${p.id}`, bot: "jony", side: p.side, strike: p.strike,
      expiryMs: p.expiry_ms, contracts: p.qty,
      spot: p.coin === "BTC" ? btcSpot : (jonyChart?.coins["ETH"]?.klines?.at(-1)?.close ?? null),
      entryCreditUsd: p.entry_credit * p.qty, openedAtMs: p.opened_at_ms,
      currentMarkUsd: p.current_mark_usd, unrealizedPnlUsd: p.unrealized_pnl_usd,
    })),
  ], [jonyOpenPositions, jonyChart, btcSpot]);

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">Options Fleet</h1>
            <p className="text-xs text-slate-500">BUBU · Jony — paper</p>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        <ActiveContractsRail contracts={allContracts} now={now} />
        {bubuState && <BubuSummaryRail state={bubuState} recentCycles={bubuRecentCycles} />}
        <MissionControl />

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
                  {jonyParams.put_gen_by_coin ? (
                    <span>
                      PUT ETH: vol≥{jonyParams.put_gen_by_coin.ETH?.vol_threshold} · {jonyParams.put_gen_by_coin.ETH?.regime_filter.join("/")}
                      {" · "}BTC: vol≥{jonyParams.put_gen_by_coin.BTC?.vol_threshold} · {jonyParams.put_gen_by_coin.BTC?.regime_filter.join("/")}
                      {jonyParams.ret_7d_threshold != null && <> · ret7d±{jonyParams.ret_7d_threshold}%</>}
                    </span>
                  ) : (
                    <span>PUT: vol≥{jonyParams.put_gen.vol_threshold} · {jonyParams.put_gen.regime_filter.join("/")} · MTF {jonyParams.put_gen.mtf_direction_filter}</span>
                  )}
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

            {jonyProximity && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <ProximityGauge coin="ETH" data={jonyProximity.ETH} />
                <ProximityGauge coin="BTC" data={jonyProximity.BTC} />
              </div>
            )}

            <EquityChart
              points={jonyEquityHistory}
              startEquity={jonyState.start_equity_usd}
              label="JONY"
              sublabel="realized + mark-to-market"
              accentDot="bg-sky-400"
            />

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
                        {p.unrealized_pnl_usd != null && (
                          <span className={`font-mono text-xs font-bold ${
                            p.unrealized_pnl_usd >= 0 ? "text-emerald-400" : "text-rose-400"
                          }`}>
                            {fmtUsd(p.unrealized_pnl_usd)}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="text-right">
                          <p className="text-xs text-slate-400">{fmtRemaining(p.hold_h, p.opened_at_ms)}</p>
                          <p className="text-[10px] text-slate-600 mt-0.5">{p.option_symbol} · TP2 {(p.tp2_pct * 100).toFixed(0)}% / SL {(p.sl_pct * 100).toFixed(0)}%</p>
                        </div>
                        <button
                          onClick={() => closeOneJonyPosition(p.id)}
                          disabled={closingJonyIds.has(p.id)}
                          className="px-2 py-1 text-[10px] font-semibold rounded-lg bg-rose-900/50 hover:bg-rose-800/70
                                     disabled:opacity-40 disabled:cursor-not-allowed transition-colors shrink-0"
                        >
                          {closingJonyIds.has(p.id) ? "…" : "Закрыть"}
                        </button>
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

        {/* ───────────────────── FUTURES: BUBU (separate service, own API :8300) ───────────────────── */}
        {/* Own category, deliberately separate from the options bots above —
            long-only spot/perp grid, not a short-premium seller: no strike,
            no expiry, no ITM/OTM. Risk here is distance-to-liquidation, not
            moneyness, so it gets its own control center rather than being
            squeezed into ActiveContractsRail's options-shaped Contract model. */}
        <div className="pt-4 flex items-center gap-3">
          <h2 className="text-sm font-bold text-amber-300 uppercase tracking-widest">
            Futures
          </h2>
          <div className="h-px flex-1 bg-gradient-to-r from-amber-500/40 to-transparent" />
          <span className="text-[10px] text-slate-600 uppercase tracking-wide">long-only · BTC perp grid</span>
        </div>
        <div className="-mt-1">
          <h3 className="text-xs font-semibold text-slate-500">
            BUBU <span className="text-slate-600 font-normal">· grid DCA + range scalp (paper, v1 baseline)</span>
          </h3>
        </div>

        {bubuError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            BUBU unreachable: {bubuError}
          </div>
        )}

        {bubuState && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(bubuState.equity_usd)}
                sub={`${bubuState.equity_usd - bubuState.start_balance_usdt >= 0 ? "+" : ""}${fmtUsd(bubuState.equity_usd - bubuState.start_balance_usdt)}${bubuState.unrealized_usd !== 0 ? ` · нереал ${bubuState.unrealized_usd >= 0 ? "+" : ""}${fmtUsd(bubuState.unrealized_usd)}` : ""}`}
                accent={bubuState.equity_usd >= bubuState.start_balance_usdt ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard
                label="Win Rate"
                value={bubuState.win_rate != null ? `${bubuState.win_rate.toFixed(0)}%` : "—"}
                sub={`${bubuState.wins}W / ${bubuState.losses}L`}
              />
              <StatCard
                label="Cycles closed"
                value={`${bubuState.n_closed}`}
                sub={bubuState.open_cycle ? `level ${bubuState.open_cycle.levels_reached}, ${bubuState.leverage}x` : "no open cycle"}
              />
              <StatCard
                label="Max DD"
                value={`${bubuState.max_dd_pct.toFixed(1)}%`}
                sub={bubuState.paused ? "PAUSED" : "armed"}
              />
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={toggleBubuPause}
                disabled={bubuBusy}
                className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700
                           disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {bubuState.paused ? "Resume" : "Pause"}
              </button>
              {bubuState.open_cycle && (
                <button
                  onClick={closeBubuOpenPosition}
                  disabled={bubuBusy}
                  className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-900/50 hover:bg-rose-800/70
                             disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                >
                  {bubuBusy ? "…" : "Закрыть позицию"}
                </button>
              )}
            </div>

            <EquityChart
              points={bubuEquityHistory}
              startEquity={bubuState.start_balance_usdt}
              label="BUBU"
              accentDot="bg-amber-400"
            />

            {bubuKlines.length > 1 && (
              <BubuChart klines={bubuKlines} overlay={bubuOverlay} symbol={bubuState.symbol} />
            )}

            {bubuState.open_cycle && (
              <BubuLadder cycle={bubuState.open_cycle} spot={bubuKlines.at(-1)?.close ?? null} symbol={bubuState.symbol} />
            )}

            {bubuRecentCycles.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал циклов</span>
                  <span>{bubuRecentCycles.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {bubuRecentCycles.map((c) => {
                    const net = c.grid_pnl + c.range_pnl - c.funding_paid - c.fees_paid;
                    const isWin = net > 0;
                    return (
                      <div key={c.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            c.end_reason === "bust" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {c.end_reason ?? "?"}
                          </span>
                          <span className="text-xs text-slate-500">level {c.levels_reached}</span>
                          <span className="text-xs text-slate-500">{c.end_ts ? fmtDay(c.end_ts) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{c.range_trades} range trades</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtUsd(net)}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {!bubuState.open_cycle && bubuRecentCycles.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Waiting for the first grid cycle to open...</p>
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

