"use client";

import type { Opportunity } from "../lib/api";

export function OpportunityCard({ op, rank }: { op: Opportunity; rank: number }) {
  const isCall = op.side === "Call";
  const sideColor = isCall ? "text-emerald-400" : "text-rose-400";
  const sideBg = isCall ? "bg-emerald-500/10" : "bg-rose-500/10";
  const borderColor =
    op.scoring.score >= 7
      ? "border-emerald-500/50"
      : op.scoring.score >= 5
        ? "border-amber-500/40"
        : "border-rose-500/40";
  const signalColor =
    op.scoring.score >= 7 ? "neon-green-text" : op.scoring.score >= 5 ? "neon-yellow-text" : "neon-red-text";

  return (
    <article className={`glass-panel border ${borderColor} p-6 flex flex-col gap-5`}>
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] uppercase tracking-widest text-slate-500 font-bold">
              #{rank}
            </span>
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
          <div className="text-xs text-slate-500 mt-0.5 font-mono">{op.symbol}</div>
        </div>

        <div className="text-right">
          <div className={`text-3xl font-black ${signalColor} leading-none`}>
            {op.scoring.score.toFixed(1)}
          </div>
          <div className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mt-1">
            {op.scoring.signal}
          </div>
        </div>
      </div>

      {/* Entry plan — main attraction */}
      <div className="bg-slate-900/50 rounded-xl p-4 border border-slate-700/50">
        <h3 className="text-[11px] uppercase tracking-widest text-slate-400 font-bold mb-3">
          План входа
        </h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <KV label="Лимит-цена" value={`${op.entry_plan.limit_price.toFixed(4)}`} accent="text-emerald-300 text-lg font-bold" />
          <KV label="Контрактов" value={`${op.entry_plan.contracts} шт`} accent="text-white text-lg font-bold" />
          <KV label="Max риск" value={`$${op.entry_plan.max_risk_usd.toFixed(2)}`} accent="text-rose-300" />
          <KV label="Горизонт" value={`~${op.entry_plan.time_horizon_h}ч`} />
          <KV
            label="Take profit (premium)"
            value={op.entry_plan.take_profit_premium.toFixed(4)}
            accent="text-emerald-300"
          />
          <KV
            label="Stop loss (premium)"
            value={op.entry_plan.stop_loss_premium.toFixed(4)}
            accent="text-rose-300"
          />
          <KV
            label="Target spot"
            value={`$${op.entry_plan.target_spot.toFixed(2)}`}
            accent="text-emerald-200"
          />
          <KV
            label="Stop spot"
            value={`$${op.entry_plan.stop_spot.toFixed(2)}`}
            accent="text-rose-200"
          />
        </div>
        <p className="text-[11px] text-slate-500 mt-3 leading-relaxed">{op.entry_plan.limit_price_hint}</p>
      </div>

      {/* Context */}
      <div className="grid grid-cols-3 gap-3 text-xs">
        <Mini label="До страйка" value={`${op.distance.distance_percent.toFixed(2)}%`} sub={`$${op.distance.distance_usd}`} />
        <Mini
          label="Theta"
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
        <Mini label="IV" value={`${op.greeks.iv}%`} />
        <Mini label="Δ Delta" value={op.greeks.delta.toFixed(2)} />
        <Mini label="OI / V24h" value={`${op.liquidity.open_interest}`} sub={`v ${op.liquidity.volume_24h}`} />
      </div>

      {/* Breakdown */}
      <details className="group">
        <summary className="cursor-pointer text-[11px] uppercase tracking-widest text-slate-400 font-bold hover:text-white">
          Разбор оценки ({op.scoring.breakdown.length})
        </summary>
        <ul className="mt-3 space-y-1.5 text-xs">
          {op.scoring.breakdown.map((b, i) => (
            <li key={i} className="flex justify-between gap-3">
              <span className="text-slate-400">{b.factor}</span>
              <span
                className={`font-mono font-bold ${b.points > 0 ? "text-emerald-400" : "text-rose-400"}`}
              >
                {b.points > 0 ? "+" : ""}
                {b.points}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </article>
  );
}

function KV({ label, value, accent = "text-white" }: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">{label}</div>
      <div className={`font-mono ${accent}`}>{value}</div>
    </div>
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
