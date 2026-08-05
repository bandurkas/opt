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
  avg: "#e2b93b",
  tp: "#34d399",
  liq: "#fb7185",
  fillOld: "rgba(148, 163, 184, 0.55)",
  fillNew: "rgba(226, 185, 59, 0.9)",
  rangeBand: "rgba(56, 189, 248, 0.5)",
  grid: "rgba(148, 163, 184, 0.06)",
};

// BUBU holds at most ONE open cycle (grid DCA + range scalp on BTCUSDT
// perp) — but that one cycle can carry MULTIPLE filled grid levels (the
// "ladder"), each at its own price. This draws every level as its own line
// (fading from grey → amber, oldest → newest fill — the same visual idea as
// Jony's stacked "SELL PUT ENTRY" lines for a multi-leg position), plus the
// three numbers that actually matter for risk: AVG (the blended cost basis
// TP is measured from), TP (exit target), and LIQ (the line that ends the
// cycle badly — drawn in the same red as a losing PnL, always visible so
// distance-to-liquidation is a glance, not a mental subtraction).
export default function BubuChart({
  klines,
  overlay,
  symbol,
}: {
  klines: Kline[];
  overlay: BubuChartOverlay | null;
  symbol: string;
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
      // Ladder: one line per filled grid level, oldest→newest fades grey→amber
      // so the eye reads "how deep and how recent" at once.
      const n = overlay.fills.length;
      overlay.fills.forEach((f, i) => {
        const t = n > 1 ? i / (n - 1) : 1;
        const color = t > 0.5 ? ACCENT.fillNew : ACCENT.fillOld;
        priceLinesRef.current.push(
          series.createPriceLine({
            price: f.price, color, lineWidth: 1, lineStyle: LineStyle.Dotted,
            axisLabelVisible: false, title: `L${f.level ?? ""}`,
          }),
        );
      });

      if (overlay.range_top != null && overlay.range_bottom != null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: overlay.range_top, color: ACCENT.rangeBand, lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: true, title: "RANGE TOP",
          }),
          series.createPriceLine({
            price: overlay.range_bottom, color: ACCENT.rangeBand, lineWidth: 1, lineStyle: LineStyle.Dashed,
            axisLabelVisible: true, title: "RANGE BOT",
          }),
        );
      }

      priceLinesRef.current.push(
        series.createPriceLine({
          price: overlay.avg_price, color: ACCENT.avg, lineWidth: 2, lineStyle: LineStyle.Solid,
          axisLabelVisible: true, title: `AVG · L${overlay.levels_reached}`,
        }),
        series.createPriceLine({
          price: overlay.tp_price, color: ACCENT.tp, lineWidth: 2, lineStyle: LineStyle.Dashed,
          axisLabelVisible: true, title: "TP",
        }),
      );

      if (overlay.liq_price != null) {
        priceLinesRef.current.push(
          series.createPriceLine({
            price: overlay.liq_price, color: ACCENT.liq, lineWidth: 2, lineStyle: LineStyle.Solid,
            axisLabelVisible: true, title: "LIQUIDATION",
          }),
        );
      }
    }
  }, [klines, overlay]);

  const liqDistancePct = overlay?.liq_price
    ? ((overlay.avg_price - overlay.liq_price) / overlay.avg_price) * 100
    : null;

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full rounded-full bg-amber-400 led-armed" />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            BUBU <span className="text-slate-600">{"// "}{symbol.replace(/USDT$/, "")} PERP GRID</span>
          </h3>
        </div>
        {liqDistancePct != null && (
          <span className="text-[10px] text-slate-500 tracking-wide font-mono">
            запас до ликвидации: <span className={liqDistancePct < 15 ? "text-rose-400 font-bold" : "text-slate-400"}>
              {liqDistancePct.toFixed(1)}%
            </span>
          </span>
        )}
      </div>
      <div ref={containerRef} className="h-80 w-full" />
      {overlay && (
        <div className="px-4 py-2.5 border-t border-slate-800/60 grid grid-cols-4 gap-2 text-[11px]">
          <LegendStat label="avg" value={`$${overlay.avg_price.toFixed(1)}`} color="text-amber-300" />
          <LegendStat label="tp" value={`$${overlay.tp_price.toFixed(1)}`} color="text-emerald-400" />
          <LegendStat
            label="liq"
            value={overlay.liq_price != null ? `$${overlay.liq_price.toFixed(1)}` : "—"}
            color="text-rose-400"
          />
          <LegendStat label="уровней" value={`${overlay.levels_reached}`} color="text-slate-300" />
        </div>
      )}
    </div>
  );
}

function LegendStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div>
      <p className="text-slate-600 uppercase tracking-widest text-[9px]">{label}</p>
      <p className={`font-mono font-bold ${color}`}>{value}</p>
    </div>
  );
}
