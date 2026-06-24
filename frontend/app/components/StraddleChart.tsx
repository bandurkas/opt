"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type ISeriesPrimitive,
  type IPrimitivePaneView,
  type SeriesAttachedParameter,
  type UTCTimestamp,
} from "lightweight-charts";
import type { CanvasRenderingTarget2D } from "fancy-canvas";
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

// A straddle's two legs profit in OPPOSITE directions around the same
// strike (Call wants price down, Put wants price up) — drawing both legs'
// risk/reward zones as full-width bands on the same price axis would
// visually contradict each other where they overlap. Each leg instead gets
// its own narrow lane near the chart's right edge: a green band from the
// current price to its approx take-profit price, a red band to its approx
// stop price, both back-solved server-side (bs.implied_spot) since the
// real SL/TP trigger is on option premium, not a price level.
class RiskRewardZonesPrimitive implements ISeriesPrimitive {
  private _series: ISeriesApi<"Candlestick"> | null = null;
  private _requestUpdate: (() => void) | null = null;
  private _legs: StraddleChartLeg[] = [];
  private _currentPrice: number | null = null;

  attached(param: SeriesAttachedParameter): void {
    this._series = param.series as ISeriesApi<"Candlestick">;
    this._requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this._series = null;
    this._requestUpdate = null;
  }

  update(legs: StraddleChartLeg[], currentPrice: number | null): void {
    this._legs = legs;
    this._currentPrice = currentPrice;
    this._requestUpdate?.();
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [
      {
        renderer: () => ({
          draw: (target: CanvasRenderingTarget2D) => {
            target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
              this._draw(ctx, mediaSize.width);
            });
          },
        }),
      },
    ];
  }

  private _draw(ctx: CanvasRenderingContext2D, width: number) {
    const series = this._series;
    const currentPrice = this._currentPrice;
    if (!series || currentPrice == null) return;

    const laneWidth = 118;
    const gap = 8;
    const rightMargin = 6;

    this._legs.forEach((leg, i) => {
      if (leg.tp_price_approx == null && leg.sl_price_approx == null) return;
      const x1 = width - rightMargin - i * (laneWidth + gap);
      const x0 = x1 - laneWidth;
      const yCur = series.priceToCoordinate(currentPrice);
      if (yCur == null) return;

      const drawBand = (target: number | null, color: string, fillAlpha: string, label: string) => {
        if (target == null) return;
        const yTarget = series.priceToCoordinate(target);
        if (yTarget == null) return;
        const top = Math.min(yCur, yTarget);
        const bottom = Math.max(yCur, yTarget);
        ctx.fillStyle = fillAlpha;
        ctx.fillRect(x0, top, laneWidth, Math.max(1, bottom - top));
        ctx.strokeStyle = color;
        ctx.lineWidth = 1;
        ctx.strokeRect(x0 + 0.5, top + 0.5, laneWidth - 1, Math.max(1, bottom - top) - 1);

        const pct = ((target - currentPrice) / currentPrice) * 100;
        const labelY = yTarget < yCur ? top - 6 : bottom + 14;
        drawPill(ctx, x0 + laneWidth / 2, labelY, `${label} ${fmtUsd(target, 0)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%)`, color);
      };

      drawBand(leg.tp_price_approx, ACCENT.tp, "rgba(52, 211, 153, 0.12)", "TP≈");
      drawBand(leg.sl_price_approx, ACCENT.sl, "rgba(251, 113, 133, 0.12)", "SL≈");

      // Leg badge + live PnL / R:R readout anchored at the current-price line.
      const pnl = leg.entry_credit_usd - (leg.current_mark_usd ?? leg.entry_credit_usd);
      const rr = leg.risk_per_contract_usd > 0 ? leg.reward_per_contract_usd / leg.risk_per_contract_usd : null;
      ctx.strokeStyle = "rgba(148, 163, 184, 0.5)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x0, yCur);
      ctx.lineTo(x1, yCur);
      ctx.stroke();
      ctx.setLineDash([]);

      const pnlColor = pnl >= 0 ? ACCENT.tp : ACCENT.sl;
      const rrText = rr != null ? ` · R:R ${rr.toFixed(2)}` : "";
      drawPill(ctx, x0 + laneWidth / 2, yCur - 16, `${leg.leg} PnL ${pnl >= 0 ? "+" : ""}${fmtUsd(pnl)}${rrText}`, pnlColor);
    });
  }
}

function drawPill(ctx: CanvasRenderingContext2D, cx: number, cy: number, text: string, color: string) {
  ctx.font = "10px var(--font-geist-mono), monospace";
  const padX = 5, padY = 3;
  const w = ctx.measureText(text).width + padX * 2;
  const h = 8 + padY * 2;
  const x = cx - w / 2;
  const y = cy - h / 2;
  ctx.fillStyle = "rgba(15, 23, 42, 0.92)";
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  roundRect(ctx, x, y, w, h, 4);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cx, cy + 0.5);
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

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
  const zonesRef = useRef<RiskRewardZonesPrimitive | null>(null);

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
    const zones = new RiskRewardZonesPrimitive();
    series.attachPrimitive(zones);

    chartRef.current = chart;
    seriesRef.current = series;
    zonesRef.current = zones;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      zonesRef.current = null;
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
    }

    const currentPrice = klines[klines.length - 1].close;
    zonesRef.current?.update(legs, currentPrice);
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
          zones are approx — premium-based SL/TP, recalculated live
        </span>
      </div>
      <div ref={containerRef} className="h-72 w-full" />
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
