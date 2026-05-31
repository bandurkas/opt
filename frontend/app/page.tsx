"use client";

import { useEffect, useState } from "react";
import {
  fetchEquityHistory,
  fetchPaperConditions,
  fetchPaperPositions,
  fetchPaperState,
  type EquityPoint,
  type PaperConditions,
  type PaperPosition,
  type PaperState,
} from "./lib/api";
import { MissedSignals } from "./components/MissedSignals";

const REFRESH_MS = 15_000;

// ─────────────────────────── formatters ──────────────────────────
const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtPct = (v: number | null, d = 2) =>
  v === null || v === undefined
    ? "—"
    : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
const fmtTime = (ms: number) =>
  new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";
const fmtDuration = (ms: number) => {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
};

// ─────────────────────────── conditions ─────────────────────────
function whyBlocked(c: PaperConditions): string[] {
  const reasons: string[] = [];
  const volTh = Math.round((c.thresholds?.vol_threshold ?? 0.5) * 100);
  const regimeList = c.thresholds?.regime_filter?.join(" / ") ?? "range";
  const mtfMin = c.thresholds?.mtf_min_aligned ?? 2;
  const mtfDir = c.thresholds?.mtf_direction_filter ?? "up";
  const bullMax = c.thresholds?.bull_market_ratio_max;

  if (!c.vol_high) {
    const pct = Math.round((c.vol_pctile ?? 0) * 100);
    reasons.push(
      `Волатильность слишком низкая — ${pct}-й перцентиль, нужно ≥ ${volTh}`,
    );
  }
  if (!c.regime_ok) {
    reasons.push(
      `Режим рынка «${c.regime ?? "?"}» не подходит — нужно ${regimeList}`,
    );
  }
  const mtfOk = c.mtf_direction_ok ?? c.mtf_down_aligned;
  if (!mtfOk) {
    reasons.push(
      `MTF тренд не ${mtfDir} — сейчас ${c.mtf_direction ?? "?"} ${c.mtf_aligned_count ?? 0}/3 ТФ; нужно ${mtfDir} И ≥ ${mtfMin}/3`,
    );
  }
  if (bullMax !== null && bullMax !== undefined && !c.bull_filter_ok) {
    reasons.push(
      `EMA50/EMA200 = ${(c.ema_ratio ?? 0).toFixed(3)} > ${bullMax} (слишком сильный bull)`,
    );
  }
  return reasons;
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

// ─────────────────────────── main page ──────────────────────────
export default function Dashboard() {
  const [state, setState] = useState<PaperState | null>(null);
  const [conditions, setConditions] = useState<PaperConditions | null>(null);
  const [openPositions, setOpenPositions] = useState<PaperPosition[]>([]);
  const [recentTrades, setRecentTrades] = useState<PaperPosition[]>([]);
  const [equity, setEquity] = useState<EquityPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const load = async () => {
    try {
      const [s, c, op, rec, eq] = await Promise.all([
        fetchPaperState(),
        fetchPaperConditions(),
        fetchPaperPositions("open"),
        fetchPaperPositions("recent", 100),
        fetchEquityHistory(168),
      ]);
      setState(s);
      setConditions(c);
      setOpenPositions(op.positions);
      setRecentTrades(rec.positions);
      setEquity(eq.points);
      setLastUpdate(new Date());
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

  const reasons = conditions ? whyBlocked(conditions) : [];

  return (
    <main className="max-w-7xl mx-auto p-4 md:p-8 flex flex-col gap-6">
      <PageHeader lastUpdate={lastUpdate} />
      {error && (
        <div className="glass-panel p-4 border border-rose-500/40 text-rose-300 text-sm">
          Ошибка подключения к API: {error}
        </div>
      )}
      {!conditions && !error && (
        <div className="glass-panel p-8 text-center text-slate-400">
          Загрузка...
        </div>
      )}

      {conditions && state && (
        <>
          <SignalBanner conditions={conditions} reasons={reasons} />
          <StrategySummary conditions={conditions} />
          <ConditionsGrid conditions={conditions} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <BalanceCard state={state} />
            <StatsCard state={state} />
          </div>

          <EquityCard points={equity} />

          <OpenPositionsCard positions={openPositions} />
          <RecentTradesCard trades={recentTrades} />

          <MissedSignals />

          <StrategyExplainer conditions={conditions} />

          <footer className="text-[11px] text-slate-500 leading-relaxed pt-2 border-t border-slate-800/60">
            Симуляция Black-Scholes; реальные Bybit-цены могут отличаться ±20%.
            Лоты по 0.1 ETH, IM ≈ 10% × strike + премия, spread 2% round-trip,
            taker 0.03%. Бюджет на сделку 15% equity в маржу, лимит 80%
            одновременно. После 5 убытков подряд автоматическая пауза 12h.
          </footer>
        </>
      )}
    </main>
  );
}

// ─────────────────────────── header ─────────────────────────────
function PageHeader({ lastUpdate }: { lastUpdate: Date | null }) {
  return (
    <header className="flex flex-col md:flex-row justify-between items-start md:items-end gap-3">
      <div>
        <h1 className="text-3xl md:text-4xl font-black tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400">
          ETH Options Assistant
        </h1>
        <p className="text-slate-400 text-sm mt-1">
          Sell-Put стратегия на бычьем рынке · paper-trading на Bybit · $400 старт
        </p>
      </div>
      <div className="text-xs text-slate-400 font-mono flex items-center gap-2">
        <span className="relative inline-flex w-2 h-2">
          <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-60" />
          <span className="relative inline-block w-2 h-2 rounded-full bg-emerald-400" />
        </span>
        {lastUpdate
          ? `обновлено ${lastUpdate.toLocaleTimeString("ru-RU")}`
          : "ожидание данных…"}
      </div>
    </header>
  );
}

// ─────────────────────────── signal banner ──────────────────────
function SignalBanner({
  conditions,
  reasons,
}: {
  conditions: PaperConditions;
  reasons: string[];
}) {
  const ready = conditions.ready;
  return (
    <section
      className={`glass-panel p-6 border ${
        ready
          ? "border-emerald-500/50 bg-emerald-500/10"
          : "border-slate-700/40 bg-slate-800/20"
      }`}
    >
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="flex items-center gap-3">
          {ready ? (
            <>
              <span className="relative inline-flex w-4 h-4 shrink-0">
                <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-75" />
                <span className="relative inline-block w-4 h-4 rounded-full bg-emerald-400" />
              </span>
              <span className="text-2xl font-bold text-emerald-200">
                ВХОД АКТУАЛЕН СЕЙЧАС
              </span>
            </>
          ) : (
            <>
              <span className="inline-block w-4 h-4 rounded-full bg-slate-500 shrink-0" />
              <span className="text-xl font-semibold text-slate-300">
                Ждём подходящих условий
              </span>
            </>
          )}
        </div>
        {conditions.spot !== null && (
          <div className="text-right font-mono">
            <div className="text-2xl font-bold text-slate-100">
              ${conditions.spot.toFixed(2)}
            </div>
            <div className="text-[11px] text-slate-500">
              ETH spot · {new Date(conditions.checked_at_ms).toLocaleTimeString("ru-RU")}
            </div>
          </div>
        )}
      </div>

      {!ready && reasons.length > 0 && (
        <div className="mt-4 space-y-1.5">
          <div className="text-[11px] uppercase tracking-widest text-slate-500 font-bold">
            Почему не входим
          </div>
          <ul className="space-y-1">
            {reasons.map((r, i) => (
              <li
                key={i}
                className="text-sm text-slate-300 flex gap-2 leading-snug"
              >
                <span className="text-rose-400 shrink-0">•</span>
                <span>{r}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// ─────────────────────────── strategy summary ───────────────────
function StrategySummary({ conditions }: { conditions: PaperConditions }) {
  const t = conditions.thresholds;
  const side = "Put"; // active LIVE config
  const items = [
    { k: "side", v: `Sell ${side}`, hint: "обязуемся купить если рынок упадёт" },
    { k: "MTF", v: t?.mtf_direction_filter ?? "up", hint: "тренд по 3 ТФ" },
    { k: "regime", v: (t?.regime_filter ?? []).join("/") || "range", hint: "не сильный тренд" },
    {
      k: "vol",
      v: `≥ ${Math.round((t?.vol_threshold ?? 0.5) * 100)} pct`,
      hint: "high-vol окно",
    },
    {
      k: "bull-cap",
      v: t?.bull_market_ratio_max === null || t?.bull_market_ratio_max === undefined
        ? "off"
        : `≤ ${t?.bull_market_ratio_max}`,
      hint: "EMA50/EMA200 фильтр",
    },
  ];
  return (
    <section className="glass-panel p-4 md:p-5">
      <div className="text-[11px] uppercase tracking-widest text-slate-500 font-bold mb-3">
        Активная стратегия (LIVE_GEN_KWARGS)
      </div>
      <div className="flex flex-wrap gap-2">
        {items.map((it) => (
          <div
            key={it.k}
            className="rounded-lg border border-slate-700/50 bg-slate-900/40 px-3 py-1.5 text-xs flex flex-col"
          >
            <div className="flex items-baseline gap-2">
              <span className="text-slate-500">{it.k}</span>
              <span className="font-mono text-slate-100">{it.v}</span>
            </div>
            <span className="text-[10px] text-slate-500">{it.hint}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

// ─────────────────────────── conditions grid ────────────────────
function ConditionsGrid({ conditions }: { conditions: PaperConditions }) {
  const t = conditions.thresholds;
  const bullActive = t?.bull_market_ratio_max !== null && t?.bull_market_ratio_max !== undefined;

  return (
    <section className={`grid grid-cols-1 ${bullActive ? "md:grid-cols-4" : "md:grid-cols-3"} gap-3 text-xs`}>
      <ConditionPill
        label="Высокая волатильность"
        ok={conditions.vol_high}
        detail={
          conditions.vol_pctile !== null
            ? `${Math.round((conditions.vol_pctile ?? 0) * 100)}-й перцентиль`
            : "—"
        }
        need={`≥ ${Math.round((t?.vol_threshold ?? 0.5) * 100)} pct`}
      />
      <ConditionPill
        label="Режим: range"
        ok={conditions.regime_ok}
        detail={conditions.regime ?? "—"}
        need={(t?.regime_filter ?? ["range"]).join(" / ")}
      />
      <ConditionPill
        label={`MTF ${t?.mtf_direction_filter ?? "up"}`}
        ok={conditions.mtf_direction_ok ?? conditions.mtf_down_aligned}
        detail={`${conditions.mtf_direction ?? "—"} · ${
          conditions.mtf_aligned_count ?? 0
        }/3 TF`}
        need={`${t?.mtf_direction_filter ?? "up"} + ≥ ${t?.mtf_min_aligned ?? 2}/3`}
      />
      {bullActive && (
        <ConditionPill
          label="Не bull-перегрев"
          ok={conditions.bull_filter_ok}
          detail={
            conditions.ema_ratio !== null
              ? `EMA50/200 = ${conditions.ema_ratio.toFixed(3)}`
              : "—"
          }
          need={`≤ ${t?.bull_market_ratio_max}`}
        />
      )}
    </section>
  );
}

function ConditionPill({
  label,
  ok,
  detail,
  need,
}: {
  label: string;
  ok: boolean;
  detail: string;
  need: string;
}) {
  return (
    <div
      className={`rounded-lg px-3 py-2 border ${
        ok
          ? "bg-emerald-500/10 border-emerald-500/30"
          : "bg-slate-700/30 border-slate-600/30"
      }`}
    >
      <div className="flex items-center gap-2">
        <span
          className={
            ok
              ? "text-emerald-400 text-base shrink-0"
              : "text-slate-500 text-base shrink-0"
          }
        >
          {ok ? "✓" : "✕"}
        </span>
        <span className="font-semibold text-slate-200 text-xs">{label}</span>
      </div>
      <div className="mt-1 text-slate-400 text-xs">{detail}</div>
      <div className="text-[10px] text-slate-500 mt-0.5">{need}</div>
    </div>
  );
}

// ─────────────────────────── balance card ───────────────────────
function BalanceCard({ state }: { state: PaperState }) {
  const change = state.current_equity_usd - state.start_equity_usd;
  const changePct = (change / state.start_equity_usd) * 100;
  const isUp = change >= 0;
  const realized = state.realized_usd ?? 0;
  const unrealized = state.unrealized_usd ?? 0;
  const maxDd = state.max_dd_pct ?? 0;
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">
        Текущий баланс (paper)
      </div>
      <div
        className={`text-5xl font-bold ${
          isUp ? "neon-green-text" : "neon-red-text"
        }`}
      >
        {fmtUsd(state.current_equity_usd)}
      </div>
      <div className="mt-2 text-sm">
        <span className={isUp ? "text-emerald-400" : "text-rose-400"}>
          {isUp ? "+" : ""}
          {fmtUsd(change, 2)} ({fmtPct(changePct)})
        </span>
        <span className="text-slate-500">
          {" "}
          от старта {fmtUsd(state.start_equity_usd)}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <MiniStat
          label="Realized"
          value={`${realized >= 0 ? "+" : ""}${fmtUsd(realized, 2)}`}
          accent={realized >= 0 ? "text-emerald-300" : "text-rose-300"}
        />
        <MiniStat
          label="Unrealized"
          value={`${unrealized >= 0 ? "+" : ""}${fmtUsd(unrealized, 2)}`}
          accent={unrealized >= 0 ? "text-emerald-300" : "text-rose-300"}
        />
        <MiniStat
          label="Max DD"
          value={`${maxDd.toFixed(2)}%`}
          accent={
            maxDd < 5
              ? "text-slate-300"
              : maxDd < 15
                ? "text-amber-300"
                : "text-rose-300"
          }
        />
      </div>
      {state.cb_active && (
        <div className="mt-3 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
          ⏸ Circuit breaker активен — после 5 убытков пауза 12h.
        </div>
      )}
      <SignalFreshness state={state} />
    </div>
  );
}

function MiniStat({
  label,
  value,
  accent = "text-slate-200",
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className="bg-slate-900/40 border border-slate-700/40 rounded p-2">
      <div className="text-[10px] uppercase tracking-widest text-slate-500">
        {label}
      </div>
      <div className={`font-mono mt-0.5 ${accent}`}>{value}</div>
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
      : "bg-slate-500/10 border-slate-500/30 text-slate-300";

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
          Рынок тихий — нет setup&apos;а. Это норма, ждём волатильности.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── stats card ────────────────────────
function StatsCard({ state }: { state: PaperState }) {
  const items = [
    { label: "Сделок закрыто", value: state.n_closed.toString() },
    { label: "Сейчас открыто", value: state.n_open.toString() },
    {
      label: "Win rate",
      value: state.win_rate !== null ? `${(state.win_rate * 100).toFixed(1)}%` : "—",
    },
    {
      label: "Avg P&L / trade",
      value: state.n_closed > 0 ? fmtPct(state.avg_pnl_pct) : "—",
    },
    { label: "Реализовано", value: fmtUsd(state.realized_usd) },
    { label: "Wins / Losses", value: `${state.wins} / ${state.losses}` },
  ];
  const ec = state.exit_counts ?? {};
  const exitItems = [
    { label: "TP1", value: ec.tp1 ?? 0, color: "text-emerald-300" },
    { label: "TP2", value: ec.tp2 ?? 0, color: "text-emerald-400" },
    { label: "SL", value: ec.sl ?? 0, color: "text-rose-400" },
    { label: "Time", value: ec.time_stop ?? 0, color: "text-slate-300" },
  ];
  const hasExits = exitItems.some((e) => e.value > 0);
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        Статистика
      </div>
      <div className="grid grid-cols-2 gap-y-3 gap-x-6">
        {items.map((it) => (
          <div key={it.label}>
            <div className="text-xs text-slate-500">{it.label}</div>
            <div className="text-lg font-semibold text-slate-100">
              {it.value}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-3 border-t border-slate-700/40">
        <div className="text-[10px] uppercase tracking-widest text-slate-500 mb-2">
          Причины выхода{" "}
          {!hasExits && (
            <span className="text-slate-600 normal-case">
              (пока без сделок)
            </span>
          )}
        </div>
        <div className="grid grid-cols-4 gap-2 text-xs">
          {exitItems.map((e) => (
            <div
              key={e.label}
              className="bg-slate-900/40 border border-slate-700/40 rounded p-2 text-center"
            >
              <div className="text-[10px] text-slate-500">{e.label}</div>
              <div
                className={`text-lg font-mono font-bold ${
                  e.value > 0 ? e.color : "text-slate-600"
                }`}
              >
                {e.value}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────── equity chart ──────────────────────
function EquityCard({ points }: { points: EquityPoint[] }) {
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        Equity curve · 7 дней
      </div>
      <EquityChart points={points} />
    </div>
  );
}

function EquityChart({ points }: { points: EquityPoint[] }) {
  if (points.length < 2) {
    return (
      <div className="text-slate-400 text-sm">
        Накапливаем данные — график появится через несколько часов.
      </div>
    );
  }
  const eqs = points.map((p) => p.equity);
  const min = Math.min(...eqs);
  const max = Math.max(...eqs);
  const range = Math.max(0.01, max - min);
  const w = 800;
  const h = 180;
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
          <stop
            offset="0%"
            stopColor={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"}
            stopOpacity="0.35"
          />
          <stop
            offset="100%"
            stopColor={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"}
            stopOpacity="0"
          />
        </linearGradient>
      </defs>
      <path
        d={`${path} L${pad + (points.length - 1) * dx},${h - pad} L${pad},${h - pad} Z`}
        fill="url(#eq-grad)"
      />
      <path
        d={path}
        stroke={isUp ? "rgb(52 211 153)" : "rgb(251 113 133)"}
        strokeWidth="2"
        fill="none"
      />
    </svg>
  );
}

// ─────────────────────────── positions ─────────────────────────
function OpenPositionsCard({ positions }: { positions: PaperPosition[] }) {
  if (!positions.length) {
    return (
      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">
          Открытые позиции
        </div>
        <div className="text-slate-400 text-sm">
          Сейчас нет открытых позиций. Бот ждёт следующий сигнал.
        </div>
      </div>
    );
  }
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        Открытые позиции ({positions.length})
      </div>
      <div className="space-y-3">
        {positions.map((p) => (
          <PositionDetailCard key={p.id} p={p} isOpen={true} />
        ))}
      </div>
    </div>
  );
}

function RecentTradesCard({ trades }: { trades: PaperPosition[] }) {
  const closed = trades.filter((t) => t.status.startsWith("closed_"));
  if (!closed.length) {
    return (
      <div className="glass-panel p-6">
        <div className="text-xs uppercase tracking-wider text-slate-400 mb-2">
          История сделок
        </div>
        <div className="text-slate-400 text-sm">Пока ни одной закрытой сделки.</div>
      </div>
    );
  }
  return (
    <div className="glass-panel p-6">
      <div className="text-xs uppercase tracking-wider text-slate-400 mb-4">
        История сделок ({closed.length})
      </div>
      <div className="space-y-3 max-h-[1000px] overflow-y-auto pr-1">
        {closed.map((t) => (
          <PositionDetailCard key={t.id} p={t} isOpen={false} />
        ))}
      </div>
    </div>
  );
}

function PositionDetailCard({
  p,
  isOpen,
}: {
  p: PaperPosition;
  isOpen: boolean;
}) {
  const ageMs = (p.closed_at_ms || Date.now()) - p.opened_at_ms;
  const ageH = ageMs / 3_600_000;
  const remainingH = isOpen ? Math.max(0, p.hold_h - ageH) : 0;
  const status = statusLabel(p.status);
  const sideRu = p.side === "C" ? "Call (обязуемся продать)" : "Put (обязуемся купить)";
  const directionWord = p.side === "C" ? "ниже" : "выше";
  const pnlPos = (p.pnl_pct || 0) > 0;
  const expiryDays = (p.expiry_ms - p.opened_at_ms) / 86_400_000;

  const entryExplanation =
    p.side === "C"
      ? `Бот ПРОДАЛ Call-опцион — обязательство продать ETH по $${p.strike.toFixed(0)} если покупатель захочет. За это получил ${fmtUsd(p.entry_credit_usd)} с контракта.`
      : `Бот ПРОДАЛ Put-опцион — обязательство купить ETH по $${p.strike.toFixed(0)} если продавец захочет. За это получил ${fmtUsd(p.entry_credit_usd)} с контракта.`;
  const winCondition = `Прибыль если ETH останется ${directionWord} $${p.strike.toFixed(0)} к экспирации (или премия упадёт раньше — выкупим дешевле).`;

  return (
    <div className="border border-slate-700/50 rounded-xl p-4 bg-slate-900/40">
      <div className="flex justify-between items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`px-2 py-0.5 rounded text-[10px] font-bold tracking-wider ${status.color} bg-slate-800/60`}
            >
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
            <div
              className={`text-2xl font-bold ${
                pnlPos ? "neon-green-text" : "neon-red-text"
              }`}
            >
              {pnlPos ? "+" : ""}
              {fmtUsd(p.pnl_usd || 0)}
            </div>
            <div
              className={`text-xs font-semibold ${
                pnlPos ? "text-emerald-400" : "text-rose-400"
              }`}
            >
              {fmtPct(p.pnl_pct)} от премии
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <DetailField
          label="Когда зашли"
          value={fmtTime(p.opened_at_ms)}
          sub={`ETH был $${p.underlying_at_open.toFixed(2)}`}
        />
        <DetailField
          label="Размер"
          value={`${p.contracts.toFixed(2)} ETH`}
          sub={`маржа ${fmtUsd(p.size_usd)}`}
        />
        <DetailField
          label="Премия / strike"
          value={fmtUsd(p.entry_credit_usd)}
          sub={`${fmtPct(p.entry_credit_pct)} от спота`}
        />
        <DetailField
          label="Экспирация"
          value={fmtTime(p.expiry_ms)}
          sub={`через ${expiryDays.toFixed(1)} дн`}
        />
      </div>

      <div className="mt-4 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
        <DetailField
          label={`TP1 (-${(p.tp1_pct * 100).toFixed(0)}%)`}
          value={`премия ≤ ${fmtUsd(p.entry_credit_usd * (1 - p.tp1_pct))}`}
          sub="закрыть 50%"
        />
        <DetailField
          label={`TP2 (-${(p.tp2_pct * 100).toFixed(0)}%)`}
          value={`премия ≤ ${fmtUsd(p.entry_credit_usd * (1 - p.tp2_pct))}`}
          sub="закрыть остаток"
        />
        <DetailField
          label={`SL (+${(p.sl_pct * 100).toFixed(0)}%)`}
          value={`премия ≥ ${fmtUsd(p.entry_credit_usd * (1 + p.sl_pct))}`}
          sub="stop-loss"
        />
        <DetailField
          label={isOpen ? "До тайм-стопа" : "Держали"}
          value={
            isOpen
              ? fmtDuration(remainingH * 3_600_000)
              : fmtDuration(ageMs)
          }
          sub={isOpen ? `макс ${p.hold_h}h` : ""}
        />
      </div>

      {!isOpen && p.exit_debit_usd !== null && (
        <div className="mt-4 pt-3 border-t border-slate-700/40 grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
          <DetailField
            label="Когда вышли"
            value={p.closed_at_ms ? fmtTime(p.closed_at_ms) : "—"}
          />
          <DetailField label="Цена выкупа" value={fmtUsd(p.exit_debit_usd)} />
          <DetailField
            label="Причина"
            value={(p.exit_reason || "").toUpperCase()}
            sub={exitReasonExplain(p.exit_reason)}
          />
        </div>
      )}
    </div>
  );
}

function DetailField({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </div>
      <div className="text-slate-200 font-mono">{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function exitReasonExplain(r: string | null): string {
  switch (r) {
    case "tp1":
      return "тейк-профит #1";
    case "tp2":
      return "полный тейк-профит";
    case "sl":
      return "стоп-лосс";
    case "time_stop":
      return "вышли по таймеру";
    default:
      return "";
  }
}

// ─────────────────────────── explainer ─────────────────────────
function StrategyExplainer({ conditions }: { conditions: PaperConditions }) {
  const t = conditions.thresholds;
  const tp1 = 50;
  const tp2 = 70;
  const sl = 150;
  const holdH = 96;

  return (
    <section className="glass-panel p-6 text-sm text-slate-400 leading-relaxed">
      <details>
        <summary className="cursor-pointer text-slate-200 font-semibold list-none flex items-center gap-2">
          <span className="text-slate-400 transition-transform group-open:rotate-90">
            ▶
          </span>
          Как работает стратегия (раскрыть)
        </summary>
        <div className="mt-3 space-y-3">
          <p>
            Каждые 30 секунд бот проверяет 3 условия выше. Когда ВСЕ сходятся —
            продаёт ATM <strong>Put</strong>-опцион на 7 дней (символ
            <code className="text-slate-300 mx-1">ETH-?-K-P</code>). За продажу
            получает премию — это потенциальная прибыль.
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <div className="bg-emerald-500/5 border border-emerald-500/20 rounded p-3">
              <div className="text-emerald-300 font-semibold text-xs uppercase tracking-wider">
                Выход в плюс
              </div>
              <div className="mt-2 text-xs text-slate-300">
                <strong>TP1:</strong> премия упала на {tp1}% — закрываем половину
                позиции, фиксируем {tp1}% от credit.
              </div>
              <div className="mt-1 text-xs text-slate-300">
                <strong>TP2:</strong> премия упала на {tp2}% — закрываем
                полностью, фиксируем {tp2}% от credit.
              </div>
            </div>
            <div className="bg-rose-500/5 border border-rose-500/20 rounded p-3">
              <div className="text-rose-300 font-semibold text-xs uppercase tracking-wider">
                Выход в минус
              </div>
              <div className="mt-2 text-xs text-slate-300">
                <strong>Stop-loss:</strong> премия выросла на {sl}% от credit
                (ETH упал ниже strike) — выкупаем дороже, фиксируем −{sl}% от credit.
              </div>
              <div className="mt-1 text-xs text-slate-300">
                <strong>Тайм-стоп:</strong> прошло {holdH}h — закрываем
                по текущей цене, какой бы она ни была.
              </div>
            </div>
          </div>
          <p className="text-xs">
            <span className="text-slate-300 font-semibold">Пример win.</span>{" "}
            Получили $10 за контракт, премия упала до $5 — выкупаем за $5,
            прибыль <span className="text-emerald-300">+$5</span> (+50% от credit).
            <br />
            <span className="text-slate-300 font-semibold">Пример loss.</span>{" "}
            Получили $10, ETH резко упал, премия выросла до $25 — выкупаем за $25,
            убыток <span className="text-rose-300">−$15</span> (−150% от credit).
          </p>
          <p className="text-xs">
            <span className="text-slate-300">
              На текущей конфигурации (cd=4, hold={holdH}h) бэктест 90д holdout
              показывает ~50-70 сделок/мес, +13-15% средний P&L на сделку,
              +$85-95/мес ожидаемо на $400 (после margin-cap).
            </span>{" "}
            В тихом рынке 0 сигналов несколько дней — нормально.
          </p>
          <div className="text-xs">
            Текущие пороги: vol ≥ {Math.round((t?.vol_threshold ?? 0.5) * 100)}{" "}
            pctile · режим {(t?.regime_filter ?? ["range"]).join("/")} · MTF{" "}
            {t?.mtf_direction_filter ?? "up"} ({t?.mtf_min_aligned ?? 2}/3) ·
            bull-cap {t?.bull_market_ratio_max ?? "off"}.
          </div>
        </div>
      </details>
    </section>
  );
}
