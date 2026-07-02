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
import type { JonyPosition, Kline } from "../lib/api";

const ACCENT = {
  up: "#34d399",
  down: "#fb7185",
  entry: "#94a3b8",
  strike: "#a78bfa",
  grid: "rgba(148, 163, 184, 0.06)",
};

// Unlike Tyagach (whose SL/TP ARE spot levels), Jony's TP/SL are premium
// levels (% of entry credit) — back-solving them to spot would need a live
// BS reprice like StraddleChart's primitive. So the chart draws what is
// honest on a spot axis: the strike and the entry-time spot; TP/SL live in
// the per-position footer rows instead.
export default function JonyChart({
  coin,
  klines,
  positions,
}: {
  coin: "ETH" | "BTC";
  klines: Kline[];
  positions: JonyPosition[];
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

    for (const p of positions) {
      const tag = `SELL ${p.side === "P" ? "PUT" : "CALL"}`;
      priceLinesRef.current.push(
        series.createPriceLine({
          price: p.strike, color: ACCENT.strike, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: `${tag} STRIKE`,
        }),
        series.createPriceLine({
          price: p.underlying_at_open, color: ACCENT.entry, lineWidth: 1, lineStyle: LineStyle.Dotted,
          axisLabelVisible: true, title: `${tag} ENTRY`,
        }),
      );
    }
  }, [klines, positions]);

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-violet-400 led-armed" />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            JONY <span className="text-slate-600">{"// "}{coin} SPOT</span>
          </h3>
        </div>
        <span className="text-[10px] text-slate-500 tracking-wide">
          TP/SL — уровни премии, не спота (см. строки ниже)
        </span>
      </div>
      <div ref={containerRef} className="h-72 w-full" />
      {positions.length > 0 && (
        <div className="px-4 py-3 border-t border-slate-800/60 space-y-1.5">
          {positions.map((p) => (
            <div key={p.id} className="flex justify-between text-[11px]">
              <span className="flex items-center gap-2 text-slate-400">
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-bold font-(family-name:--font-geist-mono) ${
                    p.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                  }`}
                >
                  SELL {p.side === "P" ? "PUT" : "CALL"}
                </span>
                <span className="font-(family-name:--font-geist-mono)">{p.option_symbol}</span>
                <span className="text-slate-600 font-(family-name:--font-geist-mono)">{p.qty.toFixed(2)} ct</span>
              </span>
              <span className="font-(family-name:--font-geist-mono) text-slate-500">
                credit ${(p.entry_credit * p.qty).toFixed(2)} · TP2 {(p.tp2_pct * 100).toFixed(0)}% · SL {(p.sl_pct * 100).toFixed(0)}% · hold {p.hold_h}h
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
