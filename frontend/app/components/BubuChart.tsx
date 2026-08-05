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
import type { Kline, BubuChartOverlay } from "../lib/api";

const ACCENT = {
  up: "#34d399",
  down: "#fb7185",
  avg: "#94a3b8",
  tp: "#34d399",
  grid: "rgba(148, 163, 184, 0.06)",
};

// BUBU holds at most ONE open cycle at a time (grid DCA + range scalp on
// BTCUSDT perp) — unlike Tyagach's multi-position zone book, there's just
// one avg_price line (current grid average) and one tp_price line (next
// take-profit target) to draw, no back-solving needed (both already spot
// price levels).
export default function BubuChart({
  klines,
  overlay,
}: {
  klines: Kline[];
  overlay: BubuChartOverlay | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

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
      grid: { vertLines: { color: ACCENT.grid }, horzLines: { color: ACCENT.grid } },
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

    if (overlay) {
      priceLinesRef.current.push(
        series.createPriceLine({
          price: overlay.avg_price, color: ACCENT.avg, lineWidth: 1, lineStyle: LineStyle.Dotted,
          axisLabelVisible: true, title: `AVG (L${overlay.levels_reached})`,
        }),
        series.createPriceLine({
          price: overlay.tp_price, color: ACCENT.tp, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: "TP",
        }),
      );
    }
  }, [klines, overlay]);

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-amber-400 led-armed" />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            BUBU <span className="text-slate-600">{"// "}BTC PERP GRID</span>
          </h3>
        </div>
        <span className="text-[10px] text-slate-500 tracking-wide">
          grid DCA + range scalp, paper — v1 baseline
        </span>
      </div>
      <div ref={containerRef} className="h-72 w-full" />
      {overlay && (
        <div className="px-4 py-3 border-t border-slate-800/60 flex justify-between text-[11px]">
          <span className="text-slate-400">
            level <span className="font-(family-name:--font-geist-mono)">{overlay.levels_reached}</span>
          </span>
          <span className="font-(family-name:--font-geist-mono) text-slate-500">
            avg ${overlay.avg_price.toFixed(1)} · tp ${overlay.tp_price.toFixed(1)}
          </span>
        </div>
      )}
    </div>
  );
}
