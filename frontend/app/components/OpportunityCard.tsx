"use client";

import { useState } from "react";
import type { Opportunity } from "../lib/api";

export function OpportunityCard({ op, rank }: { op: Opportunity; rank: number }) {
  const isCall = op.side === "Call";
  const sideColor = isCall ? "text-emerald-400" : "text-rose-400";
  const sideBg = isCall ? "bg-emerald-500/15" : "bg-rose-500/15";
  const borderColor =
    op.scoring.score >= 7
      ? "border-emerald-500/50"
      : op.scoring.score >= 5
        ? "border-amber-500/40"
        : "border-rose-500/40";
  const signalColor =
    op.scoring.score >= 7 ? "neon-green-text" : op.scoring.score >= 5 ? "neon-yellow-text" : "neon-red-text";

  const plan = op.entry_plan;
  const [copied, setCopied] = useState<string | null>(null);

  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      /* noop */
    }
  };

  return (
    <article className={`glass-panel border ${borderColor} p-6 flex flex-col gap-5`}>
      {/* ── Header ───────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] uppercase tracking-widest text-slate-500 font-bold">#{rank}</span>
            <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${sideBg} ${sideColor}`}>
              {op.side.toUpperCase()}
            </span>
            <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800/70 text-slate-300">
              {op.expiry}
            </span>
          </div>
          <div className="text-2xl font-black tracking-tight text-white">
            {op.side} ${op.strike.toFixed(0)}
          </div>
        </div>

        <div className="text-right">
          <div className={`text-3xl font-black ${signalColor} leading-none`}>{op.scoring.score.toFixed(1)}</div>
          <div className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mt-1">
            {op.scoring.signal}
          </div>
        </div>
      </div>

      {/* ── Что это вообще? ──────────────────────────────── */}
      <div className="bg-slate-900/60 rounded-xl p-4 border border-slate-700/40 text-sm leading-relaxed text-slate-200">
        {plan.position_summary}
      </div>

      {/* ── ЧТО ДЕЛАТЬ — main action ─────────────────────── */}
      <div className="rounded-xl p-5 bg-gradient-to-br from-emerald-500/15 to-blue-500/10 border border-emerald-500/30">
        <div className="text-[11px] uppercase tracking-widest text-emerald-300 font-bold mb-3">
          🎯 Что делать
        </div>

        <div className="text-2xl font-black text-white leading-tight">
          Купи <span className="text-emerald-300">{plan.contracts}</span>{" "}
          {plan.contracts === 1 ? "контракт" : "контракта"}
          <br />
          по цене <span className="text-emerald-300">{plan.limit_price.toFixed(2)} USDT</span>
        </div>

        <div className="mt-3 text-sm text-slate-300 leading-relaxed">
          Заплатишь всего:{" "}
          <span className="font-bold text-white text-base">${plan.total_cost_usd.toFixed(2)}</span>
          <span className="text-slate-400"> · max риск (всё что можешь потерять)</span>
        </div>

        <p className="mt-3 text-xs text-amber-300/90 leading-relaxed border-t border-emerald-500/20 pt-3">
          ⚠️ <strong>{plan.limit_price.toFixed(2)}</strong> — это <strong>премия за один контракт</strong>{" "}
          (сколько ты платишь). Это <strong>не</strong> цена ETH.
        </p>
      </div>

      {/* ── ВЫХОД ─────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded-xl p-4 bg-emerald-500/10 border border-emerald-500/30">
          <div className="text-[11px] uppercase tracking-widest text-emerald-300 font-bold mb-2">
            ✅ Закрой с прибылью
          </div>
          <ul className="text-sm space-y-1 text-slate-200">
            <li>
              когда ETH дойдёт до <strong className="text-white">${plan.target_spot.toFixed(2)}</strong>
            </li>
            <li>
              или премия вырастет до{" "}
              <strong className="text-white">{plan.take_profit_premium.toFixed(2)}</strong>
            </li>
          </ul>
          <div className="mt-3 text-sm font-bold text-emerald-300">
            прибыль ≈ +${plan.profit_at_tp_usd.toFixed(2)}
          </div>
        </div>

        <div className="rounded-xl p-4 bg-rose-500/10 border border-rose-500/30">
          <div className="text-[11px] uppercase tracking-widest text-rose-300 font-bold mb-2">
            🛑 Режь убыток
          </div>
          <ul className="text-sm space-y-1 text-slate-200">
            <li>
              если ETH дойдёт до <strong className="text-white">${plan.stop_spot.toFixed(2)}</strong>
            </li>
            <li>
              или премия упадёт до{" "}
              <strong className="text-white">{plan.stop_loss_premium.toFixed(2)}</strong>
            </li>
          </ul>
          <div className="mt-3 text-sm font-bold text-rose-300">
            потеря ≈ −${plan.loss_at_sl_usd.toFixed(2)}
          </div>
        </div>
      </div>

      <div className="text-xs text-slate-400 -mt-2 text-center">
        Горизонт сделки ~{plan.time_horizon_h}ч. Если за это время ничего не сработало — закрывай вручную.
      </div>

      {/* ── Bybit walkthrough — beginner ─────────────────── */}
      <details open className="bg-slate-900/40 rounded-xl border border-slate-700/40">
        <summary className="cursor-pointer px-4 py-3 text-sm font-bold text-blue-300 hover:text-blue-200 flex items-center justify-between">
          <span>📖 Как купить на Bybit (пошагово)</span>
          <span className="text-[11px] text-slate-500 font-mono">click to toggle</span>
        </summary>
        <div className="px-4 pb-4">
          <div className="flex items-center gap-2 my-3 text-xs">
            <span className="text-slate-400">Символ для поиска:</span>
            <code className="font-mono bg-slate-800/80 px-2 py-1 rounded text-emerald-300">
              {plan.symbol_to_search}
            </code>
            <button
              onClick={() => copy(plan.symbol_to_search, "symbol")}
              className="text-[11px] uppercase tracking-wider px-2 py-1 rounded bg-blue-500/20 text-blue-200 border border-blue-500/30 hover:bg-blue-500/30"
            >
              {copied === "symbol" ? "✓ скопировано" : "copy"}
            </button>
          </div>
          <ol className="space-y-1.5 text-sm text-slate-200 list-decimal list-inside marker:text-emerald-400 marker:font-bold">
            {plan.bybit_steps.map((step, i) => (
              <li key={i} className="leading-relaxed">
                {step}
              </li>
            ))}
          </ol>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <Pill label="Limit Price" value={plan.limit_price.toFixed(4)} onCopy={() => copy(String(plan.limit_price), "lp")} copied={copied === "lp"} />
            <Pill label="Quantity" value={String(plan.contracts)} onCopy={() => copy(String(plan.contracts), "qty")} copied={copied === "qty"} />
          </div>
        </div>
      </details>

      {/* ── Технические детали (для продвинутых) ─────────── */}
      <details className="bg-slate-900/30 rounded-xl border border-slate-700/30">
        <summary className="cursor-pointer px-4 py-3 text-[11px] uppercase tracking-widest text-slate-400 font-bold hover:text-white">
          🤓 Технические детали и разбор оценки
        </summary>
        <div className="px-4 pb-4 space-y-3">
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Mini label="До страйка" value={`${op.distance.distance_percent.toFixed(2)}%`} sub={`$${op.distance.distance_usd}`} />
            <Mini
              label="Theta риск"
              value={op.time.theta_risk}
              sub={`${op.time.hours_to_expiry}ч`}
              accent={
                op.time.theta_risk === "низкий"
                  ? "text-emerald-300"
                  : op.time.theta_risk === "средний"
                    ? "text-amber-300"
                    : "text-rose-300"
              }
            />
            <Mini label="Спред" value={`${op.quotes.spread_pct.toFixed(1)}%`} sub={`${op.quotes.bid}/${op.quotes.ask}`} />
            <Mini label="IV (волатильность)" value={`${(op.greeks.iv * 100).toFixed(0)}%`} />
            <Mini label="Delta" value={op.greeks.delta.toFixed(2)} sub="чувств. к ETH" />
            <Mini label="OI / V24h" value={`${op.liquidity.open_interest}`} sub={`v ${op.liquidity.volume_24h}`} />
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mb-2">
              Из чего сложилась оценка
            </div>
            <ul className="space-y-1 text-xs">
              {op.scoring.breakdown.map((b, i) => (
                <li key={i} className="flex justify-between gap-3">
                  <span className="text-slate-400">{b.factor}</span>
                  <span className={`font-mono font-bold ${b.points > 0 ? "text-emerald-400" : "text-rose-400"}`}>
                    {b.points > 0 ? "+" : ""}
                    {b.points}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </details>
    </article>
  );
}

function Pill({
  label,
  value,
  onCopy,
  copied,
}: {
  label: string;
  value: string;
  onCopy: () => void;
  copied: boolean;
}) {
  return (
    <button
      onClick={onCopy}
      className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-slate-800/60 border border-slate-700/50 hover:border-emerald-500/40 transition group"
    >
      <span className="text-slate-400 text-[10px] uppercase tracking-widest font-bold">{label}</span>
      <span className="font-mono text-emerald-300 font-bold">{value}</span>
      <span className="text-[10px] text-slate-500 group-hover:text-emerald-300">{copied ? "✓" : "copy"}</span>
    </button>
  );
}

function Mini({
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
    <div className="bg-slate-800/40 rounded-lg p-2.5 border border-slate-700/40">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 font-bold">{label}</div>
      <div className={`font-mono font-bold ${accent}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 font-mono">{sub}</div>}
    </div>
  );
}
