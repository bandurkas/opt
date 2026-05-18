"use client";

import type { MarketSnapshot } from "../lib/api";

export function MarketBar({ market, scanned }: { market: MarketSnapshot; scanned: number }) {
  const dirColor =
    market.direction === "bullish"
      ? "text-emerald-400"
      : market.direction === "bearish"
        ? "text-rose-400"
        : "text-slate-300";
  const dirLabel =
    market.direction === "bullish" ? "БЫЧИЙ" : market.direction === "bearish" ? "МЕДВЕЖИЙ" : "НЕЙТРАЛЬНЫЙ";
  const arrow = market.direction === "bullish" ? "▲" : market.direction === "bearish" ? "▼" : "▬";

  return (
    <section className="glass-panel p-5">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-4 text-sm">
        <Stat label="Spot ETH" value={`$${market.spot.toFixed(2)}`} accent="text-white" />
        <Stat
          label="Тренд"
          value={`${arrow} ${dirLabel}`}
          accent={dirColor}
          sub={market.momentum_strong ? "Сильный импульс" : "Слабый импульс"}
        />
        <Stat
          label="RSI 1h"
          value={market.rsi_1h.toFixed(1)}
          accent={market.rsi_1h > 60 ? "text-emerald-400" : market.rsi_1h < 40 ? "text-rose-400" : "text-slate-200"}
        />
        <Stat
          label="Δ 1h / 4h"
          value={`${fmtPct(market.change_1h_pct)} / ${fmtPct(market.change_4h_pct)}`}
          accent="text-slate-200"
        />
        <Stat
          label="Сопротивление"
          value={`$${market.nearest_resistance.toFixed(2)}`}
          accent="text-rose-300"
        />
        <Stat
          label="Поддержка"
          value={`$${market.nearest_support.toFixed(2)}`}
          accent="text-emerald-300"
        />
      </div>
      <div className="mt-3 text-xs text-slate-500 font-mono flex flex-wrap gap-x-4 gap-y-1">
        <span>EMA9: {market.ema_fast.toFixed(2)}</span>
        <span>EMA21: {market.ema_slow.toFixed(2)}</span>
        {market.volume_spike && <span className="text-amber-400">⚡ Всплеск объёма</span>}
        <span className="ml-auto">Просканировано контрактов: {scanned}</span>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  accent = "text-white",
  sub,
}: {
  label: string;
  value: string;
  accent?: string;
  sub?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] font-bold uppercase tracking-widest text-slate-400">{label}</span>
      <span className={`text-lg font-bold ${accent}`}>{value}</span>
      {sub && <span className="text-[11px] text-slate-500">{sub}</span>}
    </div>
  );
}

function fmtPct(v: number) {
  const s = v > 0 ? "+" : "";
  return `${s}${v.toFixed(2)}%`;
}
