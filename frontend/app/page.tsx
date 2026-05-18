"use client";

import { useEffect, useState } from "react";
import { fetchTop, type Side, type TopResponse } from "./lib/api";
import { MarketBar } from "./components/MarketBar";
import { OpportunityCard } from "./components/OpportunityCard";

const REFRESH_MS = 30_000;

export default function Home() {
  const [side, setSide] = useState<Side>("both");
  const [maxDistance, setMaxDistance] = useState(8);
  const [maxHours, setMaxHours] = useState(7 * 24);
  const [data, setData] = useState<TopResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = async () => {
    try {
      const r = await fetchTop({
        baseCoin: "ETH",
        side,
        maxDistancePct: maxDistance,
        maxHours,
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
        <div className="glass-panel p-10 text-center text-slate-400">
          <p className="text-xl font-bold mb-2">Сейчас нет хороших точек входа</p>
          <p className="text-sm">
            Ни один контракт не набрал минимум 4 балла. Попробуй расширить радиус страйка или
            подожди — обновление каждые 30 секунд.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {data?.top_opportunities.map((op, i) => (
          <OpportunityCard key={op.symbol} op={op} rank={i + 1} />
        ))}
      </div>

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
