"use client";

import { useState } from "react";
import type { BubuState, BubuCycle } from "../lib/api";

// BUBU is structurally NOT an options contract (no strike/expiry/ITM) — a
// grid DCA + range-scalp futures position, at most ONE open at a time. Does
// NOT reuse ActiveContractsRail's Contract/ContractChip (built around
// strike/expiry/ITM-OTM, meaningless here) — this is a purpose-built
// bot-level summary instead: open/closed count + total PnL up top (what the
// options rail shows per-contract, this shows per-bot), with a drawer for
// the per-cycle breakdown.
export default function BubuSummaryRail({
  state,
  recentCycles,
}: {
  state: BubuState;
  recentCycles: BubuCycle[];
}) {
  const [open, setOpen] = useState(false);
  const totalPnl = state.equity_usd - state.start_balance_usdt;

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="w-full text-left bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden glass-panel
                   hover:border-slate-600 transition-colors"
      >
        <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-300 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <span className={`h-2 w-2 rounded-full led-armed ${state.paused ? "bg-amber-400" : "bg-emerald-400"}`} />
            BUBU · {state.open_cycle ? "1 открыт" : "0 открыто"} · {state.n_closed} закрыто
          </span>
          <span className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">
            {state.paused ? "paused" : "live"}
          </span>
        </div>
        <div className="px-4 py-3 flex items-center justify-between">
          <div>
            <p className="text-[9px] uppercase tracking-[0.18em] text-slate-500">Σ PnL (реал.+нереал.)</p>
            <p className={`font-mono text-xl font-bold tabular-nums ${totalPnl >= 0 ? "neon-green-text" : "neon-red-text"}`}>
              {totalPnl >= 0 ? "+" : "−"}${Math.abs(totalPnl).toFixed(2)}
            </p>
          </div>
          {state.open_cycle && (
            <div className="text-right">
              <p className="text-[9px] uppercase tracking-[0.18em] text-slate-500">открытая, нереал.</p>
              <p className={`font-mono text-sm font-bold ${state.unrealized_usd >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {state.unrealized_usd >= 0 ? "+" : ""}${state.unrealized_usd.toFixed(2)}
              </p>
              <p className="text-[10px] text-slate-500 mt-0.5">
                level {state.open_cycle.levels_reached} · {state.open_cycle.leverage_used}x
              </p>
            </div>
          )}
          <div className="text-right">
            <p className="text-[9px] uppercase tracking-[0.18em] text-slate-500">win rate</p>
            <p className="font-mono text-sm text-slate-300">
              {state.win_rate != null ? `${state.win_rate.toFixed(0)}%` : "—"}
            </p>
            <p className="text-[10px] text-slate-500 mt-0.5">{state.wins}W / {state.losses}L</p>
          </div>
        </div>
      </button>

      {open && <BubuDrawer state={state} recentCycles={recentCycles} onClose={() => setOpen(false)} />}
    </>
  );
}

function BubuDrawer({
  state,
  recentCycles,
  onClose,
}: {
  state: BubuState;
  recentCycles: BubuCycle[];
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-sm h-full bg-slate-950 border-l border-slate-800 shadow-2xl overflow-y-auto drawer-panel">
        <div className="px-5 py-4 border-b border-slate-800 console-grid flex items-center justify-between">
          <div>
            <div className="font-(family-name:--font-orbitron) text-xl font-bold tracking-wider text-amber-400">
              BUBU
            </div>
            <div className="text-[11px] text-slate-500 font-mono uppercase tracking-wide mt-0.5">
              {state.symbol.replace(/USDT$/, "")} PERP GRID · {state.n_closed} сделок закрыто
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 text-xl leading-none px-2">×</button>
        </div>

        <div className="p-5 space-y-5">
          <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-center">
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Общий PnL по боту</p>
            <p className={`mt-1 font-mono text-3xl font-bold tabular-nums ${
              state.equity_usd - state.start_balance_usdt >= 0 ? "neon-green-text" : "neon-red-text"
            }`}>
              {state.equity_usd - state.start_balance_usdt >= 0 ? "+" : "−"}
              ${Math.abs(state.equity_usd - state.start_balance_usdt).toFixed(2)}
            </p>
            <p className="text-[11px] text-slate-600 mt-1">
              ${state.start_balance_usdt.toFixed(0)} → ${state.equity_usd.toFixed(2)}
            </p>
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-900/70 divide-y divide-slate-800">
            <Row label="Реализованный PnL" value={`$${state.realized_usd.toFixed(2)}`} />
            <Row
              label="Нереализованный PnL"
              value={`${state.unrealized_usd >= 0 ? "+" : ""}$${state.unrealized_usd.toFixed(2)}`}
              valueClassName={state.unrealized_usd >= 0 ? "text-emerald-400" : "text-rose-400"}
            />
            <Row label="Max drawdown" value={`${state.max_dd_pct.toFixed(1)}%`} />
            <Row label="Win rate" value={state.win_rate != null ? `${state.win_rate.toFixed(0)}% (${state.wins}W/${state.losses}L)` : "—"} />
            {state.open_cycle && (
              <>
                <Row label="Открытая — уровень" value={`${state.open_cycle.levels_reached}`} />
                <Row label="Открытая — avg price" value={`$${state.open_cycle.live_avg_price.toFixed(1)}`} />
                <Row label="Открытая — плечо" value={`${state.open_cycle.leverage_used}x`} />
              </>
            )}
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500 mb-2 px-1">
              PnL по сделкам ({recentCycles.length})
            </p>
            <div className="rounded-xl border border-slate-800 bg-slate-900/70 divide-y divide-slate-800 max-h-96 overflow-y-auto">
              {recentCycles.length === 0 && (
                <p className="px-4 py-4 text-sm text-slate-500 text-center">Ещё нет закрытых циклов</p>
              )}
              {recentCycles.map((c) => {
                const net = c.grid_pnl + c.range_pnl - c.funding_paid - c.fees_paid;
                return (
                  <div key={c.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-bold ${
                        c.end_reason === "bust" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                      }`}>
                        {c.end_reason ?? "?"}
                      </span>
                      <span className="text-xs text-slate-500">level {c.levels_reached}</span>
                    </div>
                    <span className={`font-mono text-xs font-bold ${net >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                      {net >= 0 ? "+" : ""}${net.toFixed(2)}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="px-4 py-2.5 flex items-center justify-between text-sm">
      <span className="text-slate-500 text-xs">{label}</span>
      <span className={`font-mono text-xs ${valueClassName ?? "text-slate-200"}`}>{value}</span>
    </div>
  );
}
