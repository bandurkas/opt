"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  BaselineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type UTCTimestamp,
} from "lightweight-charts";
import type { EquityPoint } from "../lib/api";

// Same profit/loss pair used everywhere else in this dashboard (candle
// up/down in JonyChart, PnL badges in the trade logs) — kept
// identical here rather than introducing a second status pair, so "green =
// good" reads the same way across every chart on the page. This is a
// diverging/status encoding (polarity around the start-equity baseline),
// not a categorical one: color is redundant with the line's position
// relative to the dashed baseline, never the only signal.
const UP = "#34d399"; // emerald-400
const DOWN = "#fb7185"; // rose-400
const GRID = "rgba(148, 163, 184, 0.06)";
const AXIS = "rgba(148, 163, 184, 0.15)";

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtPct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;

function fmtTooltipTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

type Stats = {
  current: number;
  returnPct: number;
  peak: number;
};

// Deliberately does NOT compute maxDD here: this chart's points come from
// ~10-min equity snapshots (loop.py), coarser than the backend's own
// continuously-tracked max_dd_pct (the StatCard above already shows that
// authoritative figure) -- a second, lower-resolution "maxDD" derived from
// this sparser series would disagree with it and read as a bug, not a
// second opinion. Peak has no competing figure elsewhere, so it stays.
function computeStats(points: EquityPoint[], startEquity: number): Stats {
  let peak = startEquity;
  for (const p of points) {
    peak = Math.max(peak, p.equity);
  }
  const current = points[points.length - 1]?.equity ?? startEquity;
  return {
    current,
    returnPct: startEquity > 0 ? ((current - startEquity) / startEquity) * 100 : 0,
    peak,
  };
}

export default function EquityChart({
  points,
  startEquity,
  label,
  sublabel,
  accentDot = "bg-slate-400",
}: {
  points: EquityPoint[];
  startEquity: number;
  label: string;
  sublabel?: string;
  accentDot?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Baseline"> | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; time: string; equity: string; deltaPct: string } | null>(null);

  const stats = useMemo(() => computeStats(points, startEquity), [points, startEquity]);
  const isProfit = stats.current >= startEquity;

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
      grid: { vertLines: { color: GRID }, horzLines: { color: GRID } },
      rightPriceScale: { borderColor: AXIS },
      timeScale: { borderColor: AXIS, timeVisible: true },
      crosshair: { mode: 0 },
    });

    const series = chart.addSeries(BaselineSeries, {
      baseValue: { type: "price", price: startEquity },
      topLineColor: UP,
      topFillColor1: "rgba(52, 211, 153, 0.28)",
      topFillColor2: "rgba(52, 211, 153, 0.02)",
      bottomLineColor: DOWN,
      bottomFillColor1: "rgba(251, 113, 133, 0.02)",
      bottomFillColor2: "rgba(251, 113, 133, 0.28)",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });

    series.createPriceLine({
      price: startEquity,
      color: "#475569",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "start",
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleMove = (param: MouseEventParams) => {
      if (!param.point || !param.time || param.point.x < 0) {
        setTooltip(null);
        return;
      }
      const data = param.seriesData.get(series) as { value?: number } | undefined;
      if (data?.value === undefined) {
        setTooltip(null);
        return;
      }
      const ms = (param.time as number) * 1000;
      const delta = startEquity > 0 ? ((data.value - startEquity) / startEquity) * 100 : 0;
      setTooltip({
        x: param.point.x,
        y: param.point.y,
        time: fmtTooltipTime(ms),
        equity: fmtUsd(data.value),
        deltaPct: fmtPct(delta),
      });
    };
    chart.subscribeCrosshairMove(handleMove);

    return () => {
      chart.unsubscribeCrosshairMove(handleMove);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startEquity]);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart || points.length < 2) return;

    series.setData(
      points.map((p) => ({
        time: Math.floor(p.ts_ms / 1000) as UTCTimestamp,
        value: p.equity,
      })),
    );
    chart.timeScale().fitContent();
  }, [points]);

  if (points.length < 2) return null;

  return (
    <div className="glass-panel console-grid overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-slate-800/60 flex-wrap gap-y-1.5">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className={`absolute inline-flex h-full w-full rounded-full ${accentDot} led-armed`} />
          </span>
          <h3 className="font-(family-name:--font-orbitron) text-xs font-bold tracking-[0.2em] uppercase text-slate-300">
            {label} <span className="text-slate-600">{"// "}EQUITY (PAPER)</span>
          </h3>
          {sublabel && <span className="text-[10px] text-slate-600">{sublabel}</span>}
        </div>
        <div className="flex items-center gap-4 font-mono text-[11px]">
          <span className="text-slate-500">
            peak <span className="text-slate-300">{fmtUsd(stats.peak, 0)}</span>
          </span>
          <span className={`font-bold ${isProfit ? "text-emerald-400" : "text-rose-400"}`}>
            {fmtUsd(stats.current)} ({fmtPct(stats.returnPct)})
          </span>
        </div>
      </div>
      <div className="relative">
        <div ref={containerRef} className="h-40 w-full" />
        {tooltip && (
          <div
            className="pointer-events-none absolute z-10 rounded-lg border border-slate-700 bg-slate-950/95 px-2.5 py-1.5 text-[11px] font-mono shadow-lg"
            style={{
              left: Math.min(tooltip.x + 12, (containerRef.current?.clientWidth ?? 0) - 130),
              top: 8,
            }}
          >
            <div className="text-slate-500">{tooltip.time}</div>
            <div className="text-slate-100 font-semibold">{tooltip.equity}</div>
            <div className="text-slate-400">{tooltip.deltaPct}</div>
          </div>
        )}
      </div>
    </div>
  );
}
