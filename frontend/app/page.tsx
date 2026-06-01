"use client";

import { useEffect, useState } from "react";
import { fetchPaperState, fetchPaperConditions, fetchPaperPositions, type PaperState, type PaperConditions, type PaperPosition } from "./lib/api";

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

export default function Dashboard() {
  const [state, setState] = useState<PaperState | null>(null);
  const [conditions, setConditions] = useState<PaperConditions | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, c, p] = await Promise.all([
          fetchPaperState(),
          fetchPaperConditions(),
          fetchPaperPositions("open"),
        ]);
        if (cancelled) return;
        setState(s);
        setConditions(c);
        setPositions(p.positions);
        setLastUpdate(new Date());
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (!state) return <main className="min-h-screen bg-slate-950 text-white flex items-center justify-center">Loading...</main>;

  const ret7d = conditions?.ret_7d ?? 0;
  const activeSide = conditions?.active_side;
  const deadZone = conditions?.dead_zone ?? false;
  const retPutMax = conditions?.thresholds?.ret_threshold_put ?? -2.5;
  const retCallMin = conditions?.thresholds?.ret_threshold_call ?? 1.0;

  // Distance to next signal
  let distToSignal = "";
  if (deadZone) {
    if (ret7d < 0) {
      const drop = Math.abs(retPutMax - ret7d);
      distToSignal = `${drop.toFixed(2)}% more drop to sell Put`;
    } else {
      const rise = Math.abs(retCallMin - ret7d);
      distToSignal = `${rise.toFixed(2)}% more rise to sell Call`;
    }
  } else if (activeSide === "P") {
    distToSignal = `Conditions met for Put`;
  } else if (activeSide === "C") {
    distToSignal = `Conditions met for Call`;
  }

  const change = state.current_equity_usd - state.start_equity_usd;
  const isUp = change >= 0;

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">ETH Options</h1>
            <p className="text-xs text-slate-500">Config B · 7d switching · Paper $400</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">
              {lastUpdate?.toLocaleTimeString("ru-RU")}
            </p>
            {error && <p className="text-xs text-rose-400">{error}</p>}
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        {/* Active Side Banner */}
        <div className={`rounded-xl p-4 border ${
          deadZone
            ? "bg-slate-900 border-slate-700"
            : activeSide === "P"
              ? "bg-rose-950/30 border-rose-800/50"
              : "bg-emerald-950/30 border-emerald-800/50"
        }`}>
          <div className="flex items-center justify-between">
            <div>
              {deadZone ? (
                <>
                  <p className="text-slate-400 text-sm">⏸ Dead Zone</p>
                  <p className="text-xs text-slate-500 mt-1">{distToSignal}</p>
                </>
              ) : (
                <>
                  <p className={`text-lg font-bold ${activeSide === "P" ? "text-rose-300" : "text-emerald-300"}`}>
                    SELL {activeSide === "P" ? "PUT" : "CALL"}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">{distToSignal}</p>
                </>
              )}
            </div>
            <div className="text-right">
              <p className="text-2xl font-mono font-bold">
                {ret7d > 0 ? "+" : ""}{ret7d.toFixed(2)}%
              </p>
              <p className="text-xs text-slate-500">7d return</p>
            </div>
          </div>
          {/* Progress bar to next threshold */}
          {deadZone && (
            <div className="mt-3">
              <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden relative">
                {/* Markers */}
                <div className="absolute left-0 top-0 bottom-0 w-px bg-rose-600" title="Put threshold" />
                <div className="absolute right-0 top-0 bottom-0 w-px bg-emerald-600" title="Call threshold" />
                {/* Current position */}
                <div
                  className="absolute top-0 bottom-0 w-2 bg-white rounded-full transition-all duration-500"
                  style={{
                    left: `${Math.max(0, Math.min(100, ((ret7d - retPutMax) / (retCallMin - retPutMax)) * 100))}%`,
                  }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                <span>Put &lt;{retPutMax}%</span>
                <span>Dead Zone</span>
                <span>Call &gt;{retCallMin}%</span>
              </div>
            </div>
          )}
        </div>

        {/* Stats Row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Equity" value={fmtUsd(state.current_equity_usd)} sub={`${isUp ? "+" : ""}${fmtUsd(change)} (${fmtPct((change / state.start_equity_usd) * 100)})`} accent={isUp ? "text-emerald-300" : "text-rose-300"} />
          <StatCard label="Win Rate" value={state.win_rate ? `${(state.win_rate * 100).toFixed(0)}%` : "—"} sub={`${state.wins}W / ${state.losses}L`} />
          <StatCard label="Trades" value={`${state.n_closed}`} sub={`${state.n_open} open`} />
          <StatCard label="Avg PnL" value={state.avg_pnl_pct ? fmtPct(state.avg_pnl_pct) : "—"} sub={fmtUsd(state.realized_usd)} />
        </div>

        {/* Circuit Breaker */}
        {state.cb_active && (
          <div className="bg-amber-950/30 border border-amber-800/50 rounded-xl px-4 py-3 text-sm text-amber-300">
            ⏸ Circuit breaker active · {state.consec_losses} losses · pause 48h
          </div>
        )}

        {/* Open Positions */}
        {positions.length > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
              Open Positions ({positions.length})
            </div>
            <div className="divide-y divide-slate-800">
              {positions.map((p) => (
                <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                  <div>
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      p.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                    }`}>
                      SELL {p.side}
                    </span>
                    <span className="ml-2 text-sm font-mono">${p.strike}</span>
                    <span className="ml-2 text-xs text-slate-500">{p.contracts.toFixed(2)} ETH</span>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-slate-500">{fmtRemaining(p.hold_h, p.opened_at_ms)}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* No positions */}
        {positions.length === 0 && !state.cb_active && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
            <p className="text-sm text-slate-400">No open positions</p>
            <p className="text-xs text-slate-500 mt-1">Waiting for signal...</p>
          </div>
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
