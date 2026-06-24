"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Kline, StraddleChartLeg } from "../lib/api";

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;

// Shared visual language across every straddle bot's chart panel (Boba1/
// Grogu1/Sniper1 will all render through this one component) — HUD accent
// colors, not per-bot bespoke styling.
const ACCENT = {
  up: "#34d399",
  down: "#fb7185",
  strike: "#94a3b8",
  sl: "#fb7185",
  tp: "#34d399",
  grid: "rgba(148, 163, 184, 0.06)",
};

export default function StraddleChart({
  callsign,
  symbol,
  klines,
  legs,
}: {
  callsign: string;
  symbol: string;
  klines: Kline[];
  legs: StraddleChartLeg[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  // Chart + series are created once and live for the component's lifetime —
  // only the data and price lines are replaced on every poll below.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: "#94a3b8",
        fontFamily: "var(--font-geist-mono)",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: ACCENT.grid },
        horzLines: { color: ACCENT.grid },
      },
      rightPriceScale: { borderColor: "rgba(148, 163, 184, 0.15)" },
      timeScale: { borderColor: "rgba(148, 163, 184, 0.15)", timeVisible: true },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: ACCENT.up,
      downColor: ACCENT.down,
      borderVisible: false,
      wickUpColor: ACCENT.up,
      wickDownColor: ACCENT.down,
    });

    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || klines.length === 0) return;

    series.setData(
      klines.map((k) => ({
        time: Math.floor(k.start_ms / 1000) as UTCTimestamp,
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
      })),
    );
    chart.timeScale().fitContent();

    for (const line of priceLinesRef.current) series.removePriceLine(line);
    priceLinesRef.current = [];

    const drawnStrikes = new Set<number>();
    for (const leg of legs) {
      if (!drawnStrikes.has(leg.strike)) {
        drawnStrikes.add(leg.strike);
        priceLinesRef.current.push(
          series.createPriceLine({
            price: leg.strike,
            color: ACCENT.strike,
            lineWidth: 1,
            lineStyle: LineStyle.Solid,
            axisLabelVisible: true,
            title: `STRIKE ${leg.strike}`,
          }),
        );
      }
      if (leg.sl_price_approx != null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: leg.sl_price_approx,
            color: ACCENT.sl,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `SL≈ ${leg.leg}`,
          }),
        );
      }
      if (leg.tp_price_approx != null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: leg.tp_price_approx,
            color: ACCENT.tp,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title: `TP≈ ${leg.leg}`,
          }),
        );
      }
    }
  }, [klines, legs]);

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 led-armed" />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            {callsign} <span className="text-slate-600">{"// "}{symbol} SPOT</span>
          </h3>
        </div>
        <span className="text-[10px] text-slate-500 tracking-wide">
          approx SL/TP — premium-based, recalculated live
        </span>
      </div>
      <div ref={containerRef} className="h-64 w-full" />
      {legs.length > 0 && (
        <div className="px-4 py-3 border-t border-slate-800/60 space-y-2.5">
          {legs.map((leg) => {
            const pct = leg.sl_progress_pct ?? 0;
            const danger = pct >= 80;
            const warn = pct >= 50;
            return (
              <div key={leg.id}>
                <div className="flex justify-between text-[11px] mb-1">
                  <span className="flex items-center gap-2 text-slate-400">
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-bold font-(family-name:--font-geist-mono) ${
                        leg.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                      }`}
                    >
                      {leg.leg === "P" ? "PUT" : "CALL"}
                    </span>
                    <span className="font-(family-name:--font-geist-mono)">
                      ${leg.strike} · {leg.current_mark_usd != null ? fmtUsd(leg.current_mark_usd) : "—"} / {fmtUsd(leg.entry_credit_usd)}
                    </span>
                  </span>
                  <span
                    className={`font-(family-name:--font-geist-mono) font-bold ${
                      danger ? "neon-red-text" : warn ? "text-amber-400" : "text-slate-500"
                    }`}
                  >
                    {leg.sl_progress_pct != null ? `${pct.toFixed(0)}% → SL` : "no live mark"}
                  </span>
                </div>
                <div className="h-1.5 bg-slate-800/80 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-[width] duration-500 ${
                      danger ? "bg-rose-500" : warn ? "bg-amber-400" : "bg-emerald-500"
                    }`}
                    style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
