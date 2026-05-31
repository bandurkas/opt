"use client";

import { useEffect, useState } from "react";
import { fetchMissedSignals, type MissedSignalsReport, type MissedTrade } from "../lib/api";

const LOOKBACK_OPTIONS = [7, 14, 21, 30];

export function MissedSignals() {
  const [data, setData] = useState<MissedSignalsReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lookback, setLookback] = useState(14);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const r = await fetchMissedSignals(lookback);
        if (!cancelled) setData(r);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "request failed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [lookback]);

  if (loading && !data) {
    return (
      <section className="glass-panel p-6">
        <div className="text-slate-300">
          Расчёт проверки стратегии за {lookback} дней… (1–2 мин при первом запросе, потом мгновенно)
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section className="glass-panel p-6 border border-rose-500/30 bg-rose-500/5">
        <div className="text-rose-300 text-sm">Ошибка загрузки missed-signals: {error}</div>
      </section>
    );
  }

  if (!data) return null;

  if (data.error) {
    return (
      <section className="glass-panel p-6">
        <div className="text-slate-400">Не хватает истории klines для расчёта: {data.error}</div>
      </section>
    );
  }

  const profitable = data.total_pnl_usd > 0;
  const trades = data.trades;
  // newest-first for display (backend returns oldest-first by ts_ms)
  const sortedTrades = [...trades].sort((a, b) => b.ts_ms - a.ts_ms);
  const visibleTrades = expanded ? sortedTrades : sortedTrades.slice(0, 15);

  return (
    <section className="glass-panel p-6 flex flex-col gap-5 border border-cyan-500/30">
      {/* Header */}
      <header className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-bold text-cyan-200 flex items-center gap-2">
            Проверка стратегии на последних {lookback} днях
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Что текущая LIVE-стратегия ВЫСТРЕЛИЛА БЫ на реальных Bybit-данных
            за выбранное окно: те же сигналы, тот же sizing, те же exit-правила
            (TP1 50%, TP2 70%, SL 150%, hold 96h). Цены — Black-Scholes
            (±20% vs реальный Bybit).
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-widest text-slate-500 font-bold">
            Окно
          </span>
          {LOOKBACK_OPTIONS.map((d) => {
            const isActive = lookback === d;
            const isRefetching = loading && isActive;
            return (
              <button
                key={d}
                disabled={loading}
                onClick={() => setLookback(d)}
                className={`px-3 py-1 text-xs font-bold rounded transition flex items-center gap-1 ${
                  isActive
                    ? "bg-cyan-500/30 text-cyan-100"
                    : "bg-slate-800/60 text-slate-400 border border-slate-700/50 hover:text-white"
                } ${loading ? "opacity-60 cursor-wait" : ""}`}
              >
                {isRefetching && (
                  <span className="inline-block w-2 h-2 rounded-full bg-cyan-300 animate-pulse" />
                )}
                {d}д
              </button>
            );
          })}
          {loading && (
            <span className="text-[11px] text-cyan-300/80 ml-2 animate-pulse">
              пересчёт ~45с…
            </span>
          )}
        </div>
      </header>

      {/* Summary stats grid */}
      <div className={`grid grid-cols-2 md:grid-cols-5 gap-3 transition-opacity ${loading ? "opacity-50" : ""}`}>
        <Stat
          label="Сигналов"
          value={String(data.n_signals)}
          sub={[
            `CB: ${data.n_skipped_by_cb}`,
            data.n_skipped_by_margin !== undefined && data.n_skipped_by_margin > 0
              ? `margin: ${data.n_skipped_by_margin}`
              : null,
          ].filter(Boolean).join(" · ") || `пропущено CB: ${data.n_skipped_by_cb}`}
        />
        <Stat
          label="Win Rate"
          value={data.win_rate !== null ? `${(data.win_rate * 100).toFixed(1)}%` : "—"}
          sub={`${data.wins}W / ${data.losses}L`}
          accent={data.win_rate && data.win_rate > 0.55 ? "text-emerald-300" : "text-slate-200"}
        />
        <Stat
          label="P&L USD"
          value={`${profitable ? "+" : ""}$${data.total_pnl_usd.toFixed(2)}`}
          sub={`${profitable ? "+" : ""}${data.total_pnl_pct.toFixed(1)}%`}
          accent={profitable ? "text-emerald-300" : "text-rose-300"}
        />
        <Stat
          label="Equity сейчас"
          value={`$${data.final_equity_usd.toFixed(2)}`}
          sub={`старт $${data.start_equity_usd.toFixed(0)}`}
          accent={profitable ? "text-emerald-300" : "text-rose-300"}
        />
        <Stat
          label="Avg/сделку"
          value={`${data.avg_pnl_pct_per_trade > 0 ? "+" : ""}${data.avg_pnl_pct_per_trade.toFixed(2)}%`}
          sub={Object.entries(data.resolution_counts)
            .map(([k, v]) => `${k}:${v}`)
            .join(" ")}
        />
      </div>

      {/* Equity sparkline */}
      {data.equity_curve.length > 1 && (
        <div className={`transition-opacity ${loading ? "opacity-50" : ""}`}>
          <EquitySparkline points={data.equity_curve} />
        </div>
      )}

      {/* Trade table */}
      {trades.length > 0 && (
        <div className={`mt-2 transition-opacity ${loading ? "opacity-50" : ""}`}>
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[11px] uppercase tracking-widest text-slate-400 font-bold">
              История сделок ({expanded ? trades.length : Math.min(15, trades.length)} из {trades.length})
            </h3>
            {trades.length > 15 && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="text-xs text-blue-300 hover:text-blue-200"
              >
                {expanded ? "Свернуть" : "Показать все"}
              </button>
            )}
          </div>
          <div className="overflow-x-auto rounded-lg border border-slate-700/50">
            <table className="w-full text-xs">
              <thead className="bg-slate-800/50">
                <tr className="text-slate-400">
                  <th className="text-left p-2 font-bold">Время (UTC)</th>
                  <th className="text-left p-2 font-bold">Сторона</th>
                  <th className="text-right p-2 font-bold">Strike</th>
                  <th className="text-right p-2 font-bold">Spot</th>
                  <th className="text-right p-2 font-bold">Лоты</th>
                  <th className="text-right p-2 font-bold">Маржа</th>
                  <th className="text-right p-2 font-bold">P&L %</th>
                  <th className="text-right p-2 font-bold">P&L $</th>
                  <th className="text-right p-2 font-bold">Equity</th>
                  <th className="text-left p-2 font-bold">Exit</th>
                </tr>
              </thead>
              <tbody>
                {visibleTrades.map((t) => (
                  <TradeRow key={t.ts_ms} trade={t} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Footer note */}
      <footer className="text-[11px] text-slate-500 leading-relaxed border-t border-slate-700/40 pt-3">
        {data.pricing_note}
        {data.cached && <span className="ml-2 text-slate-600">кэш {data.cache_age_s}с</span>}
      </footer>
    </section>
  );
}

function Stat({
  label,
  value,
  sub,
  accent = "text-white",
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="bg-slate-900/40 rounded-lg p-3 border border-slate-700/40">
      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">{label}</div>
      <div className={`text-lg font-bold font-mono mt-1 ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function TradeRow({ trade }: { trade: MissedTrade }) {
  const isWin = trade.pnl_usd > 0;
  const ts = new Date(trade.ts_ms);
  const ts_str =
    ts.toISOString().substring(5, 16).replace("T", " ") + " UTC";
  const sideColor = trade.side === "C" ? "text-emerald-400" : "text-rose-400";
  const sideBg = trade.side === "C" ? "bg-emerald-500/10" : "bg-rose-500/10";
  return (
    <tr className="border-t border-slate-800/50 hover:bg-slate-800/30">
      <td className="p-2 font-mono text-slate-300">{ts_str}</td>
      <td className="p-2">
        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${sideBg} ${sideColor}`}>
          {trade.side === "C" ? "SELL CALL" : "SELL PUT"}
        </span>
      </td>
      <td className="p-2 text-right font-mono">${trade.strike}</td>
      <td className="p-2 text-right font-mono text-slate-400">${trade.spot_at_entry.toFixed(2)}</td>
      <td className="p-2 text-right font-mono text-slate-300">
        {trade.n_lots !== undefined ? (
          <>
            {trade.n_lots}
            <span className="text-slate-500 text-[10px]">
              {trade.contracts_eth !== undefined ? ` (${trade.contracts_eth.toFixed(1)}ETH)` : ""}
            </span>
          </>
        ) : "—"}
      </td>
      <td className="p-2 text-right font-mono text-slate-300">${trade.size_usd.toFixed(2)}</td>
      <td className={`p-2 text-right font-mono font-bold ${isWin ? "text-emerald-300" : "text-rose-300"}`}>
        {isWin ? "+" : ""}
        {trade.pnl_pct.toFixed(2)}%
      </td>
      <td className={`p-2 text-right font-mono font-bold ${isWin ? "text-emerald-300" : "text-rose-300"}`}>
        {isWin ? "+" : ""}${trade.pnl_usd.toFixed(2)}
      </td>
      <td className="p-2 text-right font-mono text-slate-300">${trade.equity_after.toFixed(2)}</td>
      <td className="p-2 text-[10px] uppercase text-slate-500">{trade.exit_reason}</td>
    </tr>
  );
}

function EquitySparkline({
  points,
}: {
  points: { ts_ms: number; equity_usd: number; label: string }[];
}) {
  if (points.length < 2) return null;

  const W = 800;
  const H = 80;
  const PAD = 4;

  const equities = points.map((p) => p.equity_usd);
  const minE = Math.min(...equities);
  const maxE = Math.max(...equities);
  const range = Math.max(1, maxE - minE);

  const tsMin = points[0].ts_ms;
  const tsMax = points[points.length - 1].ts_ms;
  const tsRange = Math.max(1, tsMax - tsMin);

  const xy = points.map((p) => {
    const x = PAD + ((p.ts_ms - tsMin) / tsRange) * (W - 2 * PAD);
    const y = H - PAD - ((p.equity_usd - minE) / range) * (H - 2 * PAD);
    return { x, y, p };
  });

  const path = xy.map((pt, i) => `${i === 0 ? "M" : "L"} ${pt.x.toFixed(1)} ${pt.y.toFixed(1)}`).join(" ");
  const startY = xy[0].y;
  const isUp = points[points.length - 1].equity_usd > points[0].equity_usd;

  // 5 evenly-spaced time ticks (start, 25%, 50%, 75%, end)
  const N_TICKS = 5;
  const ticks = Array.from({ length: N_TICKS }, (_, i) => {
    const frac = i / (N_TICKS - 1);
    return tsMin + frac * tsRange;
  });
  const fmtDate = (ms: number) => {
    const d = new Date(ms);
    return `${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
  };
  const spanH = tsRange / 3_600_000;
  const spanLabel =
    spanH < 48
      ? `${spanH.toFixed(0)}h`
      : `${(spanH / 24).toFixed(1)}d`;

  return (
    <div className="bg-slate-900/40 rounded-lg p-3 border border-slate-700/40">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">
          Equity curve · период {spanLabel}
        </span>
        <span className="text-[11px] font-mono text-slate-400">
          ${minE.toFixed(2)} … ${maxE.toFixed(2)}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20" preserveAspectRatio="none">
        <line x1={PAD} x2={W - PAD} y1={startY} y2={startY} stroke="rgb(100,116,139)" strokeDasharray="3,3" strokeWidth="0.5" />
        <path d={path} fill="none" stroke={isUp ? "rgb(52,211,153)" : "rgb(251,113,133)"} strokeWidth="1.5" />
        {xy.map((pt, i) => (
          <circle
            key={i}
            cx={pt.x}
            cy={pt.y}
            r="1.5"
            fill={pt.p.label === "loss" ? "rgb(251,113,133)" : pt.p.label === "win" ? "rgb(52,211,153)" : "rgb(148,163,184)"}
          />
        ))}
      </svg>
      <div className="mt-1 flex justify-between text-[10px] font-mono text-slate-500 px-1">
        {ticks.map((t, i) => (
          <span key={i}>{fmtDate(t)}</span>
        ))}
      </div>
    </div>
  );
}
