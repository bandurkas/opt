"use client";

import { useEffect, useState } from "react";
import {
  fetchPaperConditions,
  fetchTop,
  type PaperConditions,
  type Side,
  type TopResponse,
} from "./lib/api";
import { EmptyState } from "./components/EmptyState";
import { MarketBar } from "./components/MarketBar";
import { OpportunityCard } from "./components/OpportunityCard";

const REFRESH_MS = 30_000;

export default function Home() {
  const [side, setSide] = useState<Side>("both");
  const [maxDistance, setMaxDistance] = useState(8);
  const [maxHours, setMaxHours] = useState(14 * 24);
  const [data, setData] = useState<TopResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [conditions, setConditions] = useState<PaperConditions | null>(null);

  const load = async () => {
    try {
      const r = await fetchTop({
        baseCoin: "ETH",
        side,
        maxDistancePct: maxDistance,
        maxHours,
        strategy: "fade_long_dated",
      });
      setData(r);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e: any) {
      setError(e?.message || "Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [side, maxDistance, maxHours]);

  useEffect(() => {
    const tick = async () => {
      try {
        const c = await fetchPaperConditions();
        setConditions(c);
      } catch {
        /* ignore */
      }
    };
    tick();
    const id = setInterval(tick, 20_000);
    return () => clearInterval(id);
  }, []);

  return (
    <main className="p-6 md:p-10 max-w-7xl mx-auto flex flex-col gap-6">
      <header className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-black tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400">
            ETH Options Assistant
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Сканирует опционы Bybit в реальном времени — ранжирует точки входа по 0–10
          </p>
        </div>
        <div className="text-xs text-slate-400 font-mono flex items-center gap-3">
          <span className="relative inline-flex w-2 h-2">
            <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-60" />
            <span className="relative inline-block w-2 h-2 rounded-full bg-emerald-400" />
          </span>
          {updatedAt ? `обновлено ${updatedAt.toLocaleTimeString("ru-RU")}` : "ожидание данных…"}
        </div>
      </header>

      {/* Paper-trader banner */}
      <section className="glass-panel p-4 border border-emerald-500/40 bg-emerald-500/5 flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className="px-2 py-1 rounded bg-emerald-500/20 text-emerald-200 font-bold text-xs">
            Стратегия помощник
          </span>
          <a href="/paper" className="text-emerald-300 hover:text-emerald-200 underline text-sm">
            Открыть paper-dashboard →
          </a>
        </div>
        {conditions && (
          <div className="flex items-center gap-2 text-xs">
            {conditions.ready ? (
              <span className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/15 border border-emerald-400/40">
                <span className="relative inline-flex w-2.5 h-2.5">
                  <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-75" />
                  <span className="relative inline-block w-2.5 h-2.5 rounded-full bg-emerald-400" />
                </span>
                <span className="text-emerald-200 font-semibold">🟢 ВХОД АКТУАЛЕН СЕЙЧАС</span>
              </span>
            ) : (
              <span className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-700/30 border border-slate-600/30">
                <span className="inline-block w-2.5 h-2.5 rounded-full bg-slate-500" />
                <span className="text-slate-400">Условия не сошлись — ждём</span>
              </span>
            )}
          </div>
        )}
      </section>

      {conditions && (
        <section className="glass-panel p-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <ConditionPill
            label="Высокая волатильность"
            ok={conditions.vol_high}
            detail={conditions.vol_pctile !== null ? `${Math.round((conditions.vol_pctile || 0) * 100)}-й перцентиль` : "—"}
            need="нужно ≥ 70"
          />
          <ConditionPill
            label="Режим рынка"
            ok={conditions.regime_ok}
            detail={conditions.regime ?? "—"}
            need="range / transition"
          />
          <ConditionPill
            label="Тренд вниз (MTF)"
            ok={conditions.mtf_down_aligned}
            detail={`${conditions.mtf_direction ?? "—"} · ${conditions.mtf_aligned_count ?? 0}/3 TF`}
            need="down + 2/3"
          />
          <ConditionPill
            label="Не bull-рынок"
            ok={conditions.bull_filter_ok}
            detail={conditions.ema_ratio !== null ? `EMA50/200 = ${conditions.ema_ratio.toFixed(3)}` : "—"}
            need="≤ 1.05"
          />
        </section>
      )}

      {/* Filters */}
      <section className="glass-panel p-4 flex flex-wrap gap-4 items-end">
        <Filter label="Сторона">
          <div className="flex gap-1">
            {(["both", "call", "put"] as Side[]).map((s) => (
              <button
                key={s}
                onClick={() => setSide(s)}
                className={`px-3 py-1.5 text-xs font-bold uppercase tracking-wider rounded-md transition ${
                  side === s
                    ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                    : "bg-slate-800/60 text-slate-400 border border-slate-700/50 hover:text-white"
                }`}
              >
                {s === "both" ? "оба" : s === "call" ? "calls" : "puts"}
              </button>
            ))}
          </div>
        </Filter>

        <Filter label={`Радиус страйка ±${maxDistance}%`}>
          <input
            type="range"
            min={2}
            max={20}
            step={0.5}
            value={maxDistance}
            onChange={(e) => setMaxDistance(parseFloat(e.target.value))}
            className="accent-emerald-500 w-36"
          />
        </Filter>

        <Filter label={`До экспирации ≤ ${formatHours(maxHours)}`}>
          <input
            type="range"
            min={12}
            max={30 * 24}
            step={6}
            value={maxHours}
            onChange={(e) => setMaxHours(parseInt(e.target.value))}
            className="accent-emerald-500 w-36"
          />
        </Filter>

        <button
          onClick={() => {
            setLoading(true);
            load();
          }}
          className="ml-auto px-4 py-2 text-xs font-bold uppercase tracking-wider rounded-md bg-blue-500/20 text-blue-200 border border-blue-500/40 hover:bg-blue-500/30"
        >
          Обновить
        </button>
      </section>

      {error && (
        <div className="glass-panel p-4 border border-rose-500/40 text-rose-300 text-sm font-mono">
          Ошибка: {error}
        </div>
      )}

      {data && <MarketBar market={data.market} scanned={data.scanned_options} />}

      {loading && !data && <Loader />}

      {data && data.top_opportunities.length === 0 && !loading && (
        <EmptyState market={data.market} watchlist={data.watchlist ?? []} />
      )}

      {data && data.top_opportunities.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          {data.top_opportunities.map((op, i) => (
            <OpportunityCard key={op.symbol} op={op} rank={i + 1} />
          ))}
        </div>
      )}

      {data && (
        <footer className="text-[11px] text-slate-500 text-center mt-4 leading-relaxed">
          {data.disclaimer}
        </footer>
      )}
    </main>
  );
}

function Filter({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] uppercase tracking-widest text-slate-400 font-bold">{label}</span>
      {children}
    </label>
  );
}

function formatHours(h: number): string {
  if (h >= 24) return `${Math.round(h / 24)}д`;
  return `${h}ч`;
}

function Loader() {
  return (
    <div className="flex items-center justify-center flex-col gap-3 py-16">
      <div className="w-10 h-10 border-4 border-emerald-500 border-t-transparent rounded-full animate-spin" />
      <p className="text-slate-400 font-mono animate-pulse">Сканирую цепочку опционов…</p>
    </div>
  );
}

function ConditionPill({
  label, ok, detail, need,
}: { label: string; ok: boolean; detail: string; need: string }) {
  return (
    <div
      className={`rounded-lg px-3 py-2 border ${
        ok
          ? "bg-emerald-500/10 border-emerald-500/30"
          : "bg-slate-700/30 border-slate-600/30"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={ok ? "text-emerald-400" : "text-slate-500"}>{ok ? "✓" : "✕"}</span>
        <span className="font-semibold text-slate-200">{label}</span>
      </div>
      <div className="mt-1 text-slate-400">{detail}</div>
      <div className="text-[10px] text-slate-500 mt-0.5">{need}</div>
    </div>
  );
}
