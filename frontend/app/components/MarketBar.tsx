"use client";

import type { MarketBlock, TFAnalysis } from "../lib/api";

export function MarketBar({ market, scanned }: { market: MarketBlock; scanned: number }) {
  const dirColor =
    market.direction === "bullish"
      ? "text-emerald-400"
      : market.direction === "bearish"
        ? "text-rose-400"
        : "text-slate-300";
  const dirLabel =
    market.direction === "bullish" ? "БЫЧИЙ" : market.direction === "bearish" ? "МЕДВЕЖИЙ" : "НЕЙТРАЛЬНЫЙ";
  const arrow = market.direction === "bullish" ? "▲" : market.direction === "bearish" ? "▼" : "▬";

  const regime = market.regime;
  const regimeColor =
    regime.regime === "trend"
      ? "text-emerald-300"
      : regime.regime === "range"
        ? "text-amber-300"
        : regime.regime === "transition"
          ? "text-slate-300"
          : "text-slate-500";
  const regimeLabel =
    regime.regime === "trend"
      ? "ТРЕНД"
      : regime.regime === "range"
        ? "ФЛЭТ"
        : regime.regime === "transition"
          ? "ПЕРЕХОД"
          : "?";

  return (
    <section className="glass-panel p-5 flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-4 text-sm">
        <Stat label="Spot ETH" value={`$${market.spot.toFixed(2)}`} accent="text-white" />
        <Stat label="Тренд" value={`${arrow} ${dirLabel}`} accent={dirColor} />
        <Stat
          label="Регим"
          value={regimeLabel}
          accent={regimeColor}
          sub={regime.adx ? `ADX ${regime.adx}` : "—"}
        />
        <Stat label="RSI 1h" value={market.rsi_1h.toFixed(1)} accent="text-slate-200" />
        <Stat label="Сопротивление" value={`$${market.nearest_resistance.toFixed(2)}`} accent="text-rose-300" />
        <Stat label="Поддержка" value={`$${market.nearest_support.toFixed(2)}`} accent="text-emerald-300" />
      </div>

      {/* MTF stack */}
      {market.mtf?.tf_5m && (
        <div className="grid grid-cols-3 gap-2 text-xs">
          <TFRow label="5m" tf={market.mtf.tf_5m} />
          <TFRow label="15m" tf={market.mtf.tf_15m} />
          <TFRow label="1h" tf={market.mtf.tf_1h} />
        </div>
      )}

      <div className="text-xs text-slate-500 font-mono flex flex-wrap gap-x-4 gap-y-1">
        {market.atr_15m !== null && <span>ATR(15m) {market.atr_15m}</span>}
        {market.mtf && (
          <span>
            MTF: {market.mtf.tfs_aligned}/{market.mtf.tfs_total} согласие · {market.mtf.direction.toUpperCase()}
          </span>
        )}
        {market.volume_spike && <span className="text-amber-400">⚡ Всплеск объёма</span>}
        <span className="ml-auto">Просканировано контрактов: {scanned}</span>
      </div>
    </section>
  );
}

function TFRow({ label, tf }: { label: string; tf: TFAnalysis }) {
  const dirArrow = tf.direction === "up" ? "▲" : tf.direction === "down" ? "▼" : "▬";
  const dirColor =
    tf.direction === "up"
      ? "text-emerald-400"
      : tf.direction === "down"
        ? "text-rose-400"
        : "text-slate-400";
  const momColor =
    tf.momentum === "accelerating"
      ? "text-emerald-300"
      : tf.momentum === "decelerating"
        ? "text-amber-300"
        : tf.momentum === "divergent"
          ? "text-rose-300"
          : "text-slate-500";

  return (
    <div className="bg-slate-800/40 rounded-lg p-2.5 border border-slate-700/40 flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">{label}</span>
        <span className={`text-base font-bold ${dirColor}`}>{dirArrow}</span>
      </div>
      <div className="flex justify-between font-mono text-[11px]">
        <span className="text-slate-400">RSI</span>
        <span className="text-slate-200">{tf.rsi !== null ? tf.rsi.toFixed(0) : "—"}</span>
      </div>
      <div className="flex justify-between font-mono text-[11px]">
        <span className="text-slate-400">EMA</span>
        <span className="text-slate-200">
          {tf.ema20 !== null && tf.ema50 !== null ? (tf.ema20 > tf.ema50 ? "20▲50" : "20▼50") : "—"}
        </span>
      </div>
      <div className={`text-[10px] font-bold uppercase tracking-wider ${momColor}`}>{tf.momentum}</div>
    </div>
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
