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
import { MissedSignals } from "../components/MissedSignals";

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
      <SignalFreshness state={state} />
    </div>
  );
}

function SignalFreshness({ state }: { state: PaperState }) {
  const age = state.last_signal_age_h;
  const n24 = state.signals_24h;

  let ageLabel: string;
  let tone: "ok" | "stale" | "none";
  if (age === null) {
    ageLabel = `нет за окно ${state.window_5m_bars} баров (~${Math.round((state.window_5m_bars * 5) / 60)}h)`;
    tone = "none";
  } else if (age < 1) {
    ageLabel = `${Math.round(age * 60)} мин назад`;
    tone = "ok";
  } else if (age < 6) {
    ageLabel = `${age.toFixed(1)}h назад`;
    tone = "ok";
  } else if (age < 24) {
    ageLabel = `${age.toFixed(1)}h назад`;
    tone = "stale";
  } else {
    ageLabel = `${(age / 24).toFixed(1)}d назад`;
    tone = "stale";
  }

  const palette =
    tone === "ok"
      ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
      : tone === "stale"
        ? "bg-slate-500/10 border-slate-500/30 text-slate-300"
        : "bg-slate-500/10 border-slate-500/30 text-slate-400";

  return (
    <div className={`mt-3 px-3 py-2 rounded-lg border text-xs ${palette}`}>
      <div className="flex items-center justify-between gap-3">
        <span>Последний сигнал генератора:</span>
        <span className="font-semibold">{ageLabel}</span>
      </div>
      <div className="flex items-center justify-between gap-3 mt-1 text-slate-400">
        <span>За последние 24h:</span>
        <span>{n24} сигнал(ов)</span>
      </div>
      {tone === "stale" && n24 === 0 && (
        <div className="mt-1 text-slate-500">
          Рынок тихий — нет setup'а для входа. Это норма, ждём волатильности.
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

function PositionDetailCard({ p, isOpen }: { p: PaperPosition; isOpen: boolean }) {
  const ageMs = (p.closed_at_ms || Date.now()) - p.opened_at_ms;
  const ageH = ageMs / 3_600_000;
  const remainingH = isOpen ? Math.max(0, p.hold_h - ageH) : 0;
  const status = statusLabel(p.status);
  const sideRu = p.side === "C" ? "Call (право купить)" : "Put (право продать)";
  const directionWord = p.side === "C" ? "выше" : "ниже";
  const pnlPos = (p.pnl_pct || 0) > 0;
  const expiryDays = (p.expiry_ms - p.opened_at_ms) / 86_400_000;

  // Plain-language explanation
  const entryExplanation = p.side === "C"
    ? `Бот ПРОДАЛ Call-опцион — обязательство продать ETH по цене $${p.strike.toFixed(0)} если кто-то захочет купить. За это получил ${fmtUsd(p.entry_credit_usd)} с одного контракта.`
    : `Бот ПРОДАЛ Put-опцион — обязательство купить ETH по цене $${p.strike.toFixed(0)} если кто-то захочет продать. За это получил ${fmtUsd(p.entry_credit_usd)} с одного контракта.`;
  const winCondition = `Прибыль если ETH останется ${directionWord} $${p.strike.toFixed(0)} к экспирации (или премия упадёт раньше — тогда выкупаем дешевле).`;

  return (
    <div className="border border-slate-700/50 rounded-xl p-4 bg-slate-900/40">
      <div className="flex justify-between items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wider ${status.color} bg-slate-800/60`}>
              {status.label}
            </span>
            <span className="text-slate-200 font-semibold">
              SELL {sideRu} @ ${p.strike.toFixed(0)}
            </span>
            <span className="text-xs text-slate-500 font-mono">#{p.id}</span>
          </div>
          <div className="text-xs text-slate-400 mt-1.5 leading-relaxed">
            {entryExplanation}
          </div>
          <div className="text-xs text-slate-500 mt-1">{winCondition}</div>
        </div>
        {!isOpen && p.pnl_pct !== null && (
          <div className="text-right shrink-0">
            <div className={`text-2xl font-bold ${pnlPos ? "neon-green-text" : "neon-red-text"}`}>
              {fmtUsd(p.pnl_usd || 0)}
            </div>
            <div className={`text-xs font-semibold ${pnlPos ? "text-emerald-400" : "text-rose-400"}`}>
              {fmtPct(p.pnl_pct)} от премии
            </div>
          </div>
        )}
      </div>

      {/* Entry block */}
      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <DetailField label="Когда зашли" value={fmtTime(p.opened_at_ms)} sub={`ETH был $${p.underlying_at_open.toFixed(2)}`} />
        <DetailField label="Размер позиции" value={`${p.contracts.toFixed(2)} ETH`} sub={`маржа ${fmtUsd(p.size_usd)}`} />
        <DetailField label="Цена премии" value={fmtUsd(p.entry_credit_usd)} sub={`${fmtPct(p.entry_credit_pct)} от спота`} />
        <DetailField
          label="Экспирация"
          value={fmtTime(p.expiry_ms)}
          sub={`через ${expiryDays.toFixed(1)} дн от входа`}
        />
      </div>

      {/* Targets / risk */}
      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <DetailField
          label="Цель TP1 (закрыть 50%)"
          value={`премия ≤ ${fmtUsd(p.entry_credit_usd * (1 - p.tp1_pct))}`}
          sub={`−${(p.tp1_pct * 100).toFixed(0)}% от credit`}
        />
        <DetailField
          label="Цель TP2 (полное закрытие)"
          value={`премия ≤ ${fmtUsd(p.entry_credit_usd * (1 - p.tp2_pct))}`}
          sub={`−${(p.tp2_pct * 100).toFixed(0)}% от credit`}
        />
        <DetailField
          label="Стоп (стоп-лосс)"
          value={`премия ≥ ${fmtUsd(p.entry_credit_usd * (1 + p.sl_pct))}`}
          sub={`+${(p.sl_pct * 100).toFixed(0)}% от credit`}
        />
        <DetailField
          label={isOpen ? "Времени до тайм-стопа" : "Сколько держали"}
          value={isOpen ? fmtDuration(remainingH * 3_600_000) : fmtDuration(ageMs)}
          sub={isOpen ? `макс ${p.hold_h}h` : `закрыта ${p.closed_at_ms ? fmtTime(p.closed_at_ms) : ""}`}
        />
      </div>

      {/* Exit info (only for closed) */}
      {!isOpen && p.exit_debit_usd !== null && (
        <div className="mt-4 pt-3 border-t border-slate-700/40 grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
          <DetailField label="Когда вышли" value={p.closed_at_ms ? fmtTime(p.closed_at_ms) : "—"} />
          <DetailField label="Цена выкупа" value={fmtUsd(p.exit_debit_usd)} sub={`выкупили обратно`} />
          <DetailField label="Причина выхода" value={(p.exit_reason || "").toUpperCase()} sub={exitReasonExplain(p.exit_reason)} />
        </div>
      )}

      {!isOpen && p.pnl_usd !== null && (
        <div className={`mt-3 p-3 rounded-lg ${pnlPos ? "bg-emerald-500/10" : "bg-rose-500/10"} text-xs leading-relaxed`}>
          <strong className={pnlPos ? "text-emerald-300" : "text-rose-300"}>Итог:</strong>{" "}
          {pnlPos
            ? `получили ${fmtUsd(p.entry_credit_usd)} за продажу, выкупили за ${fmtUsd(p.exit_debit_usd || 0)} → разница ${fmtUsd((p.entry_credit_usd - (p.exit_debit_usd || 0)))} × ${p.contracts.toFixed(4)} контр. = ${fmtUsd(p.pnl_usd)} прибыли (${fmtPct(p.pnl_pct)} от премии).`
            : `получили ${fmtUsd(p.entry_credit_usd)} за продажу, но премия выросла до ${fmtUsd(p.exit_debit_usd || 0)} → пришлось выкупать дороже, убыток ${fmtUsd(Math.abs(p.pnl_usd))} (${fmtPct(p.pnl_pct)}).`}
        </div>
      )}
    </div>
  );
}

function DetailField({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-slate-200 font-mono">{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function exitReasonExplain(r: string | null): string {
  switch (r) {
    case "tp1": return "сработал тейк-профит #1 (премия упала на 30%)";
    case "tp2": return "сработал полный тейк-профит (премия упала на 50%)";
    case "sl": return "сработал стоп-лосс (премия выросла на 50%)";
    case "time_stop": return "вышли по таймеру 24h";
    default: return "";
  }
}

function OpenPositionsCard({ positions }: { positions: PaperPosition[] }) {
  if (!positions.length) {
    return (
      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">Открытые позиции</div>
        <div className="text-slate-400 text-sm">Сейчас нет открытых позиций. Бот ждёт следующий сигнал.</div>
      </div>
    );
  }
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        Открытые позиции ({positions.length})
      </div>
      <div className="space-y-3">
        {positions.map((p) => <PositionDetailCard key={p.id} p={p} isOpen={true} />)}
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
      <div className="space-y-3 max-h-[1200px] overflow-y-auto pr-1">
        {closed.map((t) => <PositionDetailCard key={t.id} p={t} isOpen={false} />)}
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

      <OpenPositionsCard positions={openPositions} />
      <RecentTradesCard trades={recentTrades} />

      <MissedSignals />

      <div className="text-xs text-slate-500 pt-2 leading-relaxed">
        Бот торгует на бумаге по Bybit-реалистичной модели: лоты 0.1 ETH,
        начальная маржа ≈ 10% × strike + премия, спред 5% round-trip, taker-fee 0.03%
        (cap 12.5% от премии). Бюджет на сделку — 40% equity в маржу.
        Прибыль фиксируется на тейк-профитах, убыток ограничен стоп-лоссом или таймером 24 часа.
        После 3 убытков подряд автоматическая пауза на сутки.
      </div>
    </main>
  );
}
