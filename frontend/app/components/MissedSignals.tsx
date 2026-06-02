"use client";

import { useEffect, useState } from "react";
import { fetchMissedSignals, type MissedSignalsReport, type MissedTrade } from "../lib/api";

const LOOKBACK_DAYS = 30;

const fmtUsd = (v: number) => `$${v.toFixed(2)}`;
const fmtPct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const fmtDay = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
};

export function MissedSignals() {
  const [data, setData] = useState<MissedSignalsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchMissedSignals(LOOKBACK_DAYS)
      .then((r) => {
        if (!cancelled) setData(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "request failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3 text-xs text-slate-500">
        Loading 30d simulation… (~30s first time, cached after)
      </div>
    );
  }
  if (error) {
    return (
      <div className="bg-slate-900 border border-rose-800/50 rounded-xl px-4 py-3 text-xs text-rose-300">
        Sim error: {error}
      </div>
    );
  }
  if (!data || data.error) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3 text-xs text-slate-500">
        Not enough kline history: {data?.error || "no data"}
      </div>
    );
  }

  const profitable = data.total_pnl_usd > 0;
  const trades = [...data.trades].sort((a, b) => b.ts_ms - a.ts_ms).slice(0, 15);
  const nPut = data.trades.filter((t) => t.side === "P").length;
  const nCall = data.trades.filter((t) => t.side === "C").length;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
        <span>Strategy simulation (last {LOOKBACK_DAYS} days)</span>
        <span className="text-slate-600">
          {data.cached ? `cached ${data.cache_age_s}s` : "fresh"}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 px-4 py-3 border-b border-slate-800">
        <Cell label="Trades" value={`${data.n_signals}`} sub={`${nPut}P / ${nCall}C`} />
        <Cell
          label="Win Rate"
          value={data.win_rate !== null ? `${(data.win_rate * 100).toFixed(0)}%` : "—"}
          sub={`${data.wins}W / ${data.losses}L`}
          accent={data.win_rate && data.win_rate > 0.55 ? "text-emerald-300" : ""}
        />
        <Cell
          label="P&L"
          value={`${profitable ? "+" : ""}${fmtUsd(data.total_pnl_usd)}`}
          sub={fmtPct(data.total_pnl_pct)}
          accent={profitable ? "text-emerald-300" : "text-rose-300"}
        />
        <Cell
          label="Avg/trade"
          value={fmtPct(data.avg_pnl_pct_per_trade)}
          sub={`equity $${data.final_equity_usd.toFixed(0)}`}
          accent={data.avg_pnl_pct_per_trade > 0 ? "text-emerald-300" : "text-rose-300"}
        />
      </div>

      {trades.length > 0 && (
        <div className="divide-y divide-slate-800 max-h-72 overflow-y-auto">
          {trades.map((t) => (
            <TradeRow key={t.ts_ms} t={t} />
          ))}
        </div>
      )}

      <div className="px-4 py-2 text-[10px] text-slate-600 border-t border-slate-800">
        {data.pricing_note}
      </div>
    </div>
  );
}

function Cell({
  label,
  value,
  sub,
  accent = "text-slate-100",
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`text-base font-mono font-bold mt-0.5 ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function TradeRow({ t }: { t: MissedTrade }) {
  const isWin = t.pnl_usd > 0;
  return (
    <div className="px-4 py-2 flex items-center justify-between text-xs">
      <div className="flex items-center gap-2">
        <span
          className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
            t.side === "P"
              ? "bg-rose-500/10 text-rose-300"
              : "bg-emerald-500/10 text-emerald-300"
          }`}
        >
          {t.side}
        </span>
        <span className="font-mono">${t.strike}</span>
        <span className="text-slate-500">{fmtDay(t.ts_ms)}</span>
        <span className="text-[10px] uppercase text-slate-600">{t.exit_reason}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className={`font-mono ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
          {fmtPct(t.pnl_pct)}
        </span>
        <span className={`font-mono ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
          {isWin ? "+" : ""}{fmtUsd(t.pnl_usd)}
        </span>
      </div>
    </div>
  );
}
