"use client";

import { useState } from "react";
import type { Opportunity } from "../lib/api";

export function OpportunityCard({ op, rank }: { op: Opportunity; rank: number }) {
  const isCall = op.side === "Call";
  const sideColor = isCall ? "text-emerald-400" : "text-rose-400";
  const sideBg = isCall ? "bg-emerald-500/15" : "bg-rose-500/15";
  const borderColor =
    op.scoring.score >= 9
      ? "border-emerald-400/60"
      : op.scoring.score >= 7
        ? "border-emerald-500/50"
        : op.scoring.score >= 5
          ? "border-amber-500/40"
          : "border-rose-500/40";
  const signalColor =
    op.scoring.score >= 7 ? "neon-green-text" : op.scoring.score >= 5 ? "neon-yellow-text" : "neon-red-text";

  const plan = op.entry_plan;
  const exits = plan.exits;
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

  const sigType = op.scoring.signal_type;
  const sigTypeLabel =
    sigType === "fade" ? "🪞 Fade" : sigType === "pullback" ? "🔄 Pullback" : "🎯 Continuation";
  const sigTypeColor =
    sigType === "fade"
      ? "bg-violet-500/15 text-violet-300"
      : sigType === "pullback"
        ? "bg-blue-500/15 text-blue-300"
        : "bg-emerald-500/15 text-emerald-300";

  const thetaP = op.scoring.theta_decay_probability * 100;
  const thetaCls = op.scoring.theta_decay_class;
  const thetaColor =
    thetaCls === "critical"
      ? "text-rose-400"
      : thetaCls === "high"
        ? "text-rose-300"
        : thetaCls === "medium"
          ? "text-amber-300"
          : "text-emerald-300";

  return (
    <article className={`glass-panel border ${borderColor} p-6 flex flex-col gap-5`}>
      {/* ── Header ───────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center flex-wrap gap-2 mb-1">
            <span className="text-[11px] uppercase tracking-widest text-slate-500 font-bold">#{rank}</span>
            <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${sideBg} ${sideColor}`}>
              {op.side.toUpperCase()}
            </span>
            <span className="px-2 py-0.5 rounded text-[11px] font-bold bg-slate-800/70 text-slate-300">
              {op.expiry}
            </span>
            <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${sigTypeColor}`}>{sigTypeLabel}</span>
          </div>
          <div className="text-2xl font-black tracking-tight text-white">
            {op.side} ${op.strike.toFixed(0)}
          </div>
          {op.scoring.setup_reason && (
            <div className="text-xs text-blue-300/80 mt-1 italic">↳ {op.scoring.setup_reason}</div>
          )}
        </div>

        <div className="text-right">
          <div className={`text-3xl font-black ${signalColor} leading-none`}>{op.scoring.score.toFixed(1)}</div>
          <div className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mt-1">
            {op.scoring.signal}
          </div>
          <div className="text-[10px] uppercase tracking-widest text-slate-500 mt-0.5">
            {op.scoring.recommendation}
          </div>
        </div>
      </div>

      {/* ── Position summary ─────────────────────────────── */}
      <div className="bg-slate-900/60 rounded-xl p-4 border border-slate-700/40 text-sm leading-relaxed text-slate-200">
        {plan.position_summary}
      </div>

      {/* ── Action banner ────────────────────────────────── */}
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
          Заплатишь: <span className="font-bold text-white text-base">${plan.total_cost_usd.toFixed(2)}</span>
          <span className="text-slate-400"> · max риск (всё что можешь потерять)</span>
        </div>
        <p className="mt-3 text-xs text-amber-300/90 leading-relaxed border-t border-emerald-500/20 pt-3">
          ⚠️ <strong>{plan.limit_price.toFixed(2)}</strong> — это <strong>премия за один контракт</strong>{" "}
          (сколько ты платишь). Это <strong>не</strong> цена ETH.
        </p>
      </div>

      {/* ── Exit plan: TP1 / TP2 / SL ────────────────────── */}
      {exits.valid && exits.tp1 && exits.tp2 && exits.sl && (() => {
        const singleLot = (exits.tp2?.contracts_to_close ?? 0) === 0;
        // With 1 contract we close everything at TP1; recompute TP1 P&L for the full position.
        const tp1FullPnl = singleLot
          ? Math.round((exits.tp1.premium - plan.limit_price) * plan.contracts * 100) / 100
          : (exits.tp1.profit_usd ?? 0);

        return (
          <div className="space-y-3">
            <div className={`grid grid-cols-1 gap-3 ${singleLot ? "md:grid-cols-2" : "md:grid-cols-3"}`}>
              <ExitLeg
                kind="tp1"
                title={singleLot ? "✅ Take Profit (вся позиция)" : "✅ TP1 (50%)"}
                premium={exits.tp1.premium}
                spot={exits.tp1.spot}
                contracts={singleLot ? plan.contracts : exits.tp1.contracts_to_close}
                pnl={tp1FullPnl}
              />
              {!singleLot && (
                <ExitLeg
                  kind="tp2"
                  title="🏆 TP2 (остаток)"
                  premium={exits.tp2.premium}
                  spot={exits.tp2.spot}
                  contracts={exits.tp2.contracts_to_close}
                  pnl={exits.tp2.profit_usd ?? 0}
                />
              )}
              <ExitLeg
                kind="sl"
                title="🛑 Stop loss"
                premium={exits.sl.premium}
                spot={exits.sl.spot}
                contracts={plan.contracts}
                pnl={-(exits.sl.loss_usd ?? 0)}
              />
            </div>

            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
              <span>⏱ Закрой если не сработало за {exits.time_stop_hours}ч</span>
              {exits.trail_rule && <span>📈 {exits.trail_rule}</span>}
              {!singleLot && exits.summary?.risk_reward !== null && exits.summary?.risk_reward !== undefined && (
                <span className="ml-auto font-bold text-slate-300">
                  R/R = {exits.summary.risk_reward}:1
                </span>
              )}
              {singleLot && (
                <span className="ml-auto font-bold text-slate-300">
                  R/R = {(tp1FullPnl / (exits.sl?.loss_usd ?? 1)).toFixed(2)}:1
                </span>
              )}
            </div>
          </div>
        );
      })()}

      {/* ── Theta gauge ──────────────────────────────────── */}
      <div className="flex items-center gap-3 bg-slate-900/40 rounded-xl p-3 border border-slate-700/40">
        <div className="flex-1">
          <div className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mb-1">
            Шанс жертвы Theta
          </div>
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 bg-slate-800 rounded overflow-hidden">
              <div
                className={
                  thetaCls === "critical"
                    ? "h-full bg-rose-400"
                    : thetaCls === "high"
                      ? "h-full bg-rose-300"
                      : thetaCls === "medium"
                        ? "h-full bg-amber-300"
                        : "h-full bg-emerald-400"
                }
                style={{ width: `${Math.min(100, thetaP)}%` }}
              />
            </div>
            <span className={`font-mono font-bold text-sm ${thetaColor}`}>{thetaP.toFixed(0)}%</span>
          </div>
          <div className="text-[10px] text-slate-500 mt-1">
            {thetaCls === "critical"
              ? "Очень рискованно — Theta съест премию"
              : thetaCls === "high"
                ? "Серьёзный распад, бери только сильный импульс"
                : thetaCls === "medium"
                  ? "Умеренный распад — управляемо"
                  : "Низкий распад"}
          </div>
        </div>
      </div>

      {/* ── Bybit walkthrough ────────────────────────────── */}
      <details className="bg-slate-900/40 rounded-xl border border-slate-700/40">
        <summary className="cursor-pointer px-4 py-3 text-sm font-bold text-blue-300 hover:text-blue-200">
          📖 Как купить на Bybit (пошагово)
        </summary>
        <div className="px-4 pb-4">
          <div className="flex items-center gap-2 my-3 text-xs flex-wrap">
            <span className="text-slate-400">Символ:</span>
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

      {/* ── Technical details ────────────────────────────── */}
      <details className="bg-slate-900/30 rounded-xl border border-slate-700/30">
        <summary className="cursor-pointer px-4 py-3 text-[11px] uppercase tracking-widest text-slate-400 font-bold hover:text-white">
          🤓 Технические детали и разбор оценки
        </summary>
        <div className="px-4 pb-4 space-y-3">
          <div className="grid grid-cols-3 gap-2 text-xs">
            <Mini label="До страйка" value={`${op.distance.distance_percent.toFixed(2)}%`} sub={`$${op.distance.distance_usd}`} />
            <Mini label="До экспирации" value={`${op.time.hours_to_expiry}ч`} sub={op.time.theta_risk} />
            <Mini label="Спред" value={`${op.quotes.spread_pct.toFixed(1)}%`} sub={`${op.quotes.bid}/${op.quotes.ask}`} />
            <Mini label="IV (%)" value={`${(op.greeks.iv * 100).toFixed(1)}%`} sub={
              op.iv_metrics?.iv_change_1h_pct !== null
                ? `1h ${op.iv_metrics.iv_change_1h_pct! > 0 ? "+" : ""}${op.iv_metrics.iv_change_1h_pct}%`
                : "история собирается"
            } />
            <Mini label="Delta" value={op.greeks.delta.toFixed(2)} sub={`θ ${op.greeks.theta}`} />
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
                  <span className={`font-mono font-bold ${b.points > 0 ? "text-emerald-400" : b.points < 0 ? "text-rose-400" : "text-slate-500"}`}>
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

function ExitLeg({
  kind,
  title,
  premium,
  spot,
  contracts,
  pnl,
}: {
  kind: "tp1" | "tp2" | "sl";
  title: string;
  premium: number;
  spot: number;
  contracts: number;
  pnl: number;
}) {
  const isLoss = kind === "sl";
  const bg = isLoss ? "bg-rose-500/10 border-rose-500/30" : "bg-emerald-500/10 border-emerald-500/30";
  const pnlColor = isLoss ? "text-rose-300" : "text-emerald-300";
  return (
    <div className={`rounded-xl p-3 border ${bg} flex flex-col gap-1.5`}>
      <div className="text-[11px] uppercase tracking-widest font-bold text-slate-300">{title}</div>
      <div className="text-xs text-slate-200 leading-snug">
        ETH → <strong className="text-white">${spot.toFixed(2)}</strong>
        <br />
        премия → <strong className="text-white">{premium.toFixed(2)}</strong>
      </div>
      <div className="text-[11px] text-slate-400">закрыть {contracts} контракт(ов)</div>
      <div className={`text-sm font-bold ${pnlColor}`}>
        {pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}
      </div>
    </div>
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
