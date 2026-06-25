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
import type { Kline, TyagachChartZone } from "../lib/api";

const ACCENT = {
  up: "#34d399",
  down: "#fb7185",
  entry: "#94a3b8",
  sl: "#fb7185",
  tp: "#34d399",
  grid: "rgba(148, 163, 184, 0.06)",
};

const ZONE_LABEL: Record<string, string> = { OB: "Order Block", BB: "Breaker Block", MB: "Mitigation Block" };

// Unlike StraddleChart's RiskRewardZonesPrimitive (which has to back-solve a
// premium-based dollar SL into an approximate spot level via canvas lanes),
// Tyagach's stop_price/tp_price ARE spot price levels already — the
// R-multiple system operates directly on price. So this is just three
// plain price lines per open position, no custom primitive needed.
export default function TyagachChart({
  klines,
  zones,
}: {
  klines: Kline[];
  zones: TyagachChartZone[];
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

    for (const z of zones) {
      const tag = `${z.zone_kind} ${z.option_side === "P" ? "PUT" : "CALL"}`;
      priceLinesRef.current.push(
        series.createPriceLine({
          price: z.entry_spot, color: ACCENT.entry, lineWidth: 1, lineStyle: LineStyle.Dotted,
          axisLabelVisible: true, title: `${tag} ENTRY`,
        }),
        series.createPriceLine({
          price: z.stop_price, color: ACCENT.sl, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: `${tag} SL`,
        }),
        series.createPriceLine({
          price: z.tp_price, color: ACCENT.tp, lineWidth: 1, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: `${tag} TP`,
        }),
      );
    }
  }, [klines, zones]);

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-lime-400 led-armed" />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            TYAGACH <span className="text-slate-600">{"// "}ETH SPOT</span>
          </h3>
        </div>
        <span className="text-[10px] text-slate-500 tracking-wide">
          entry/SL/TP are spot levels — no premium back-solve needed
        </span>
      </div>
      <div ref={containerRef} className="h-72 w-full" />
      {zones.length > 0 && (
        <div className="px-4 py-3 border-t border-slate-800/60 space-y-1.5">
          {zones.map((z) => (
            <div key={z.id} className="flex justify-between text-[11px]">
              <span className="flex items-center gap-2 text-slate-400">
                <span
                  className={`px-1.5 py-0.5 rounded text-[10px] font-bold font-(family-name:--font-geist-mono) ${
                    z.option_side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                  }`}
                >
                  {z.option_side === "P" ? "SELL PUT" : "SELL CALL"}
                </span>
                <span className="font-(family-name:--font-geist-mono)">{ZONE_LABEL[z.zone_kind] ?? z.zone_kind}</span>
                <span className="text-slate-600 font-(family-name:--font-geist-mono)">{z.symbol}</span>
              </span>
              <span className="font-(family-name:--font-geist-mono) text-slate-500">
                entry ${z.entry_spot.toFixed(0)} · SL ${z.stop_price.toFixed(0)} · TP ${z.tp_price.toFixed(0)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
