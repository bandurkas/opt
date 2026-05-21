"use client";

import { useEffect, useState } from "react";
import {
  fetchEquityHistory,
  fetchPaperPositions,
  fetchPaperState,
  type EquityPoint,
  type PaperPosition,
  type PaperState,
} from "../lib/api";

const REFRESH_MS = 15_000;

function fmtUsd(v: number, decimals = 2): string {
  return `$${v.toFixed(decimals)}`;
}

function fmtPct(v: number | null, decimals = 2): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(decimals)}%`;
}

function fmtTime(ms: number): string {
  const d = new Date(ms);
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

function fmtDuration(ms: number): string {
  const sec = Math.floor(ms / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function statusLabel(status: string): { label: string; color: string } {
  switch (status) {
    case "open":
      return { label: "OPEN", color: "text-amber-300" };
    case "half_closed_tp1":
      return { label: "TP1 PARTIAL", color: "text-cyan-300" };
    case "closed_tp1":
      return { label: "TP1 CLOSED", color: "text-emerald-400" };
    case "closed_tp2":
      return { label: "TP2 CLOSED", color: "text-emerald-400" };
    case "closed_sl":
      return { label: "SL HIT", color: "text-rose-400" };
    case "closed_time":
      return { label: "TIME STOP", color: "text-slate-400" };
    default:
      return { label: status.toUpperCase(), color: "text-slate-400" };
  }
}

function EquityChart({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return <div className="text-slate-400 text-sm">Накапливаем данные — график появится через несколько часов.</div>;
  }
  const eqs = points.map((p) => p.equity);
  const min = Math.min(...eqs);
  const max = Math.max(...eqs);
  const range = Math.max(0.01, max - min);
  const w = 800;
  const h = 200;
  const pad = 8;
  const dx = (w - pad * 2) / Math.max(1, points.length - 1);
  const path = points
    .map((p, i) => {
      const x = pad + i * dx;
      const y = pad + (h - pad * 2) * (1 - (p.equity - min) / range);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = points[points.length - 1];
  const first = points[0];
  const isUp = last.equity >= first.equity;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"} stopOpacity="0.35" />
          <stop offset="100%" stopColor={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path
        d={`${path} L${pad + (points.length - 1) * dx},${h - pad} L${pad},${h - pad} Z`}
        fill="url(#eq-grad)"
      />
      <path d={path} stroke={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"} strokeWidth="2" fill="none" />
    </svg>
  );
}

function BalanceCard({ state }: { state: PaperState }) {
  const change = state.current_equity_usd - state.start_equity_usd;
  const changePct = (change / state.start_equity_usd) * 100;
  const isUp = change >= 0;
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">
        Текущий баланс (paper)
      </div>
      <div className={`text-5xl font-bold ${isUp ? "neon-green-text" : "neon-red-text"}`}>
        {fmtUsd(state.current_equity_usd)}
      </div>
      <div className="mt-2 text-sm">
        <span className={isUp ? "text-emerald-400" : "text-rose-400"}>
          {fmtUsd(change, 2)} ({fmtPct(changePct)})
        </span>
        <span className="text-slate-500"> от старта {fmtUsd(state.start_equity_usd)}</span>
      </div>
      {state.cb_active && (
        <div className="mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
          ⏸ Circuit breaker активен — после 3 убытков подряд пауза 24h. Сигналы не открываются.
        </div>
      )}
    </div>
  );
}

function StatsCard({ state }: { state: PaperState }) {
  const items = [
    { label: "Сделок закрыто", value: state.n_closed.toString() },
    { label: "Сейчас открыто", value: state.n_open.toString() },
    {
      label: "Win rate",
      value: state.win_rate !== null ? `${(state.win_rate * 100).toFixed(1)}%` : "—",
    },
    {
      label: "Avg P&L/trade",
      value: state.n_closed > 0 ? fmtPct(state.avg_pnl_pct) : "—",
    },
    { label: "Реализовано", value: fmtUsd(state.realized_usd) },
    { label: "Wins / Losses", value: `${state.wins} / ${state.losses}` },
  ];
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">Статистика</div>
      <div className="grid grid-cols-2 gap-y-3 gap-x-6">
        {items.map((it) => (
          <div key={it.label}>
            <div className="text-xs text-slate-500">{it.label}</div>
            <div className="text-lg font-semibold text-slate-100">{it.value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function OpenPositionsCard({ positions }: { positions: PaperPosition[] }) {
  if (!positions.length) {
    return (
      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Открытые позиции</div>
        <div className="text-slate-400 text-sm">Сейчас нет открытых позиций. Бот ждёт следующий сигнал (5m цикл).</div>
      </div>
    );
  }
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        Открытые позиции ({positions.length})
      </div>
      <div className="space-y-3">
        {positions.map((p) => {
          const ageMs = Date.now() - p.opened_at_ms;
          const ageH = ageMs / 3_600_000;
          const remainingH = Math.max(0, p.hold_h - ageH);
          const status = statusLabel(p.status);
          return (
            <div key={p.id} className="border border-slate-700/50 rounded-lg p-3 bg-slate-900/30">
              <div className="flex justify-between items-start">
                <div>
                  <div className="font-mono text-sm text-slate-200">
                    SELL {p.side} @ ${p.strike.toFixed(0)} ·{" "}
                    <span className={status.color}>{status.label}</span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    #{p.id} · opened {fmtTime(p.opened_at_ms)} · ETH was ${p.underlying_at_open.toFixed(2)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs text-slate-500">size</div>
                  <div className="text-sm font-semibold text-slate-200">{fmtUsd(p.size_usd)}</div>
                </div>
              </div>
              <div className="mt-2 grid grid-cols-4 gap-2 text-xs">
                <div>
                  <div className="text-slate-500">Credit</div>
                  <div className="text-slate-300">{fmtUsd(p.entry_credit_usd)} ({fmtPct(p.entry_credit_pct, 2)})</div>
                </div>
                <div>
                  <div className="text-slate-500">Contracts</div>
                  <div className="text-slate-300">{p.contracts.toFixed(4)}</div>
                </div>
                <div>
                  <div className="text-slate-500">TP / SL</div>
                  <div className="text-slate-300">
                    -{(p.tp1_pct * 100).toFixed(0)}/-{(p.tp2_pct * 100).toFixed(0)} / +{(p.sl_pct * 100).toFixed(0)}%
                  </div>
                </div>
                <div>
                  <div className="text-slate-500">Time left</div>
                  <div className="text-slate-300">{fmtDuration(remainingH * 3_600_000)}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RecentTradesCard({ trades }: { trades: PaperPosition[] }) {
  const closed = trades.filter((t) => t.status.startsWith("closed_"));
  if (!closed.length) {
    return (
      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">История сделок</div>
        <div className="text-slate-400 text-sm">Пока ни одной закрытой сделки.</div>
      </div>
    );
  }
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        История сделок ({closed.length})
      </div>
      <div className="space-y-2 max-h-[600px] overflow-y-auto">
        {closed.map((t) => {
          const status = statusLabel(t.status);
          const pnlPos = (t.pnl_pct || 0) > 0;
          return (
            <div
              key={t.id}
              className="flex justify-between items-center border-b border-slate-800/50 py-2 text-xs"
            >
              <div className="font-mono">
                <span className="text-slate-400">#{t.id} </span>
                <span className="text-slate-200">SELL {t.side} ${t.strike.toFixed(0)}</span>
                <span className={`ml-2 ${status.color}`}>{status.label}</span>
              </div>
              <div className="text-right">
                <div className={pnlPos ? "text-emerald-400 font-semibold" : "text-rose-400 font-semibold"}>
                  {fmtUsd(t.pnl_usd || 0)} ({fmtPct(t.pnl_pct)})
                </div>
                <div className="text-slate-500">{t.closed_at_ms ? fmtTime(t.closed_at_ms) : "—"}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function PaperPage() {
  const [state, setState] = useState<PaperState | null>(null);
  const [openPositions, setOpenPositions] = useState<PaperPosition[]>([]);
  const [recentTrades, setRecentTrades] = useState<PaperPosition[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const [s, open, recent, eq] = await Promise.all([
        fetchPaperState(),
        fetchPaperPositions("open"),
        fetchPaperPositions("recent", 100),
        fetchEquityHistory(168),
      ]);
      setState(s);
      setOpenPositions(open.positions);
      setRecentTrades(recent.positions);
      setEquity(eq.points);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <div className="p-8 text-rose-400">
        Ошибка загрузки: {error}
        <div className="text-xs text-slate-500 mt-2">
          Проверь что backend и paper сервис подняты на VPS3.
        </div>
      </div>
    );
  }

  if (!state) {
    return <div className="p-8 text-slate-400">Загрузка...</div>;
  }

  return (
    <main className="max-w-7xl mx-auto p-6 space-y-6">
      <div className="flex justify-between items-baseline">
        <h1 className="text-2xl font-bold text-slate-100">Paper Trading</h1>
        <a href="/" className="text-xs text-slate-400 hover:text-slate-200">
          ← Main dashboard
        </a>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <BalanceCard state={state} />
        <StatsCard state={state} />
      </div>

      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
          Equity curve (последние 7 дней)
        </div>
        <EquityChart points={equity} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <OpenPositionsCard positions={openPositions} />
        <RecentTradesCard trades={recentTrades} />
      </div>

      <div className="text-xs text-slate-500 pt-2">
        Стратегия: SELL ATM Call когда MTF=down + vol&gt;70%ile + regime=range/transition.
        Размер позиции = 10% от текущего equity (мин $5, макс $50).
        Exits: TP1 −30% / TP2 −50% / SL +50% / Time stop 24h.
        Bull-market filter и consecutive-loss CB активны.
      </div>
    </main>
  );
}
