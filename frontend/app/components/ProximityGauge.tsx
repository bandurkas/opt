"use client";

import type { JonyProximity } from "../lib/api";

// Zone progression reuses the app's existing status vocabulary (amber =
// caution/standby, emerald = armed/go — see MissionControl's StatusLED)
// rather than any per-bot/per-coin IDENTITY hue already active in this
// section (Jony's own positions list already uses sky=ETH/orange=BTC as
// coin badges here — reusing those for zone status would read as "which
// coin" and "how ready" at once). Waiting/preparing/ready/entry is a
// status job, not identity, so it gets its own reserved scale.
const ZONE_STYLE: Record<JonyProximity["zone"], { bar: string; text: string; label: string }> = {
  waiting: { bar: "bg-slate-600", text: "text-slate-400", label: "WAITING" },
  preparing: { bar: "bg-amber-600", text: "text-amber-400", label: "PREPARING" },
  ready: { bar: "bg-amber-400", text: "text-amber-300", label: "READY" },
  entry: { bar: "bg-emerald-400", text: "text-emerald-300", label: "ENTRY" },
  // селектор сторон не оставил ни одной (puts-only + даунтренд) — гейты не
  // считаются вовсе; это «торговать нечем», не «ждём условий» (2026-08-17)
  "side-off": { bar: "bg-slate-700", text: "text-slate-500", label: "SIDE OFF" },
};
const DEFAULT_ZONE = ZONE_STYLE.waiting; // неизвестная зона от старого/нового API — не падаем

const FACTOR_LABEL: Record<keyof JonyProximity["factors"], string> = {
  vol: "VOL", regime: "REGIME", mtf: "MTF", bull: "BULL",
};

function FactorTick({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5 flex-1 min-w-0">
      <span className="text-[9px] text-slate-600 font-mono w-12 shrink-0">{label}</span>
      <div className="h-1 flex-1 rounded-full bg-slate-800 overflow-hidden">
        <div
          className="h-full bg-slate-500 rounded-full"
          style={{ width: `${Math.round(value * 100)}%` }}
        />
      </div>
    </div>
  );
}

export default function ProximityGauge({
  coin,
  data,
}: {
  coin: "ETH" | "BTC";
  data: JonyProximity | undefined;
}) {
  if (!data) return null;
  const style = ZONE_STYLE[data.zone] ?? DEFAULT_ZONE;

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono font-bold text-slate-300">{coin}</span>
          {data.active_side && (
            <span
              className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                data.active_side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
              }`}
            >
              {data.active_side === "P" ? "PUT" : "CALL"}
            </span>
          )}
        </div>
        <span className={`text-[10px] font-mono font-bold tracking-wide ${style.text}`}>
          {style.label} {data.proximity_pct.toFixed(0)}%
        </span>
      </div>

      <div className="h-2 rounded-full bg-slate-800 overflow-hidden mb-2">
        <div
          className={`h-full rounded-full transition-[width] duration-700 ${style.bar}`}
          style={{ width: `${data.proximity_pct}%` }}
        />
      </div>

      <div className="flex gap-2.5">
        {(Object.keys(data.factors) as (keyof JonyProximity["factors"])[]).map((k) => (
          <FactorTick key={k} label={FACTOR_LABEL[k]} value={data.factors[k]} />
        ))}
      </div>

      {data.debounce_unknown && (
        <p className="text-[9px] text-slate-600 mt-1.5">debounce state unknown — capped conservative</p>
      )}
    </div>
  );
}
