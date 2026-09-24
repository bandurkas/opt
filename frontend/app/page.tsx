"use client";

import { useEffect, useState } from "react";
import { fetchBubuState, fetchBubuCycles, fetchBubuEquityHistory, fetchBubuChart, pauseBubu, resumeBubu, closeBubuPosition, type EquityPoint, type Kline, type BubuState, type BubuCycle, type BubuChartOverlay } from "./lib/api";
import MissionControl from "./components/MissionControl";
import StraddleChart from "./components/StraddleChart";
import BubuChart from "./components/BubuChart";
import BubuLadder from "./components/BubuLadder";
import EquityChart from "./components/EquityChart";
import BubuSummaryRail from "./components/BubuSummary";
import Strategy6Panel from "./components/Strategy6Panel";

const REFRESH_MS = 15_000;

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtTime = (ms: number) => {
  const d = new Date(ms);
  const now = Date.now();
  const diff = now - ms;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString();
};
const fmtDay = (ms: number) => {
  const d = new Date(ms);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${days[d.getDay()]} ${d.getDate()}`;
};
export default function Dashboard() {
  const [bubuState, setBubuState] = useState<BubuState | null>(null);
  const [bubuRecentCycles, setBubuRecentCycles] = useState<BubuCycle[]>([]);
  const [bubuEquityHistory, setBubuEquityHistory] = useState<EquityPoint[]>([]);
  const [bubuKlines, setBubuKlines] = useState<Kline[]>([]);
  const [bubuOverlay, setBubuOverlay] = useState<BubuChartOverlay | null>(null);
  const [bubuError, setBubuError] = useState<string | null>(null);
  const [bubuBusy, setBubuBusy] = useState(false);

  // BUBU — fully-separate-service pattern (own repo,
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

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">Options Fleet</h1>
            <p className="text-xs text-slate-500">BUBU · №6 A — simulation</p>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        {bubuState && <BubuSummaryRail state={bubuState} recentCycles={bubuRecentCycles} />}
        <MissionControl />
        <Strategy6Panel />

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

