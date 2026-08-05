"use client";

import type { BubuOpenCycle } from "../lib/api";

const KIND_LABEL: Record<string, string> = {
  grid: "GRID DCA",
  range_buy: "RANGE BUY",
  range_sell: "RANGE SELL",
};
const KIND_COLOR: Record<string, string> = {
  grid: "bg-amber-500/10 text-amber-300",
  range_buy: "bg-sky-500/10 text-sky-300",
  range_sell: "bg-emerald-500/10 text-emerald-300",
};

function fmtTime(ms: number) {
  const d = new Date(ms);
  return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

// The actual "position grid" for BUBU: every fill (grid DCA level + range
// scalp buy/sell) that makes up the current open cycle, newest first — the
// long-only analogue of a multi-leg options position table. PnL is broken
// out by component (grid = unrealized mark-to-market, range = already
// realized round-trips, funding/fees = pure cost) because summing them into
// one number would hide which lever is actually doing the work.
export default function BubuLadder({ cycle, spot }: { cycle: BubuOpenCycle; spot: number | null }) {
  const sortedFills = [...cycle.fills].sort((a, b) => b.ts - a.ts);
  const netUnrealized = cycle.grid_pnl_mtm + cycle.range_pnl - cycle.funding_paid - cycle.fees_paid;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex items-center justify-between">
        <span>Лестница уровней · открытый цикл</span>
        <span className={`font-mono font-bold ${netUnrealized >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
          {netUnrealized >= 0 ? "+" : ""}${netUnrealized.toFixed(2)}
        </span>
      </div>

      {/* PnL breakdown — grid mark-to-market vs range vs pure cost, not one blended number */}
      <div className="grid grid-cols-4 gap-px bg-slate-800/60 text-[11px]">
        <PnlCell label="grid (нереал.)" value={cycle.grid_pnl_mtm} />
        <PnlCell label="range (реал.)" value={cycle.range_pnl} sub={`${cycle.range_trades} сделок`} />
        <PnlCell label="funding" value={-cycle.funding_paid} invert />
        <PnlCell label="комиссии" value={-cycle.fees_paid} invert />
      </div>

      {sortedFills.length === 0 ? (
        <p className="px-4 py-5 text-sm text-slate-500 text-center">
          Только базовый вход, докупок ещё не было
        </p>
      ) : (
        <div className="divide-y divide-slate-800 max-h-64 overflow-y-auto">
          {sortedFills.map((f, i) => {
            const distPct = spot != null && f.price > 0 ? ((spot - f.price) / f.price) * 100 : null;
            return (
              <div key={`${f.ts}-${i}`} className="px-4 py-2 flex items-center justify-between text-xs">
                <div className="flex items-center gap-2">
                  <span className={`inline-block px-1.5 py-0.5 rounded text-[9px] font-bold ${KIND_COLOR[f.kind] ?? "bg-slate-700/30 text-slate-300"}`}>
                    {KIND_LABEL[f.kind] ?? f.kind}
                  </span>
                  {f.level != null && <span className="text-slate-500 font-mono">L{f.level}</span>}
                  <span className="font-mono text-slate-200">${f.price.toFixed(1)}</span>
                  <span className="text-slate-600 font-mono">{f.qty.toFixed(5)} BTC</span>
                </div>
                <div className="flex items-center gap-2">
                  {distPct != null && (
                    <span className={`font-mono text-[10px] ${distPct >= 0 ? "text-emerald-500/80" : "text-rose-500/80"}`}>
                      {distPct >= 0 ? "+" : ""}{distPct.toFixed(2)}%
                    </span>
                  )}
                  <span className="text-slate-600 text-[10px]">{fmtTime(f.ts)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PnlCell({ label, value, sub, invert }: { label: string; value: number; sub?: string; invert?: boolean }) {
  const good = invert ? value <= 0 : value >= 0;
  return (
    <div className="bg-slate-900 px-3 py-2">
      <p className="text-slate-600 uppercase tracking-widest text-[9px]">{label}</p>
      <p className={`font-mono font-bold text-sm ${good ? "text-emerald-400" : "text-rose-400"}`}>
        {value >= 0 ? "+" : ""}${value.toFixed(2)}
      </p>
      {sub && <p className="text-slate-600 text-[9px] mt-0.5">{sub}</p>}
    </div>
  );
}
