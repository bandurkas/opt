"use client";

import { useEffect, useState } from "react";
import { fetchPaperConditions, type PaperConditions } from "./lib/api";

const REFRESH_MS = 15_000;

function whyBlocked(c: PaperConditions): string[] {
  const reasons: string[] = [];
  if (!c.vol_high) {
    const pct = Math.round((c.vol_pctile ?? 0) * 100);
    reasons.push(`Волатильность слишком низкая — ${pct}-й перцентиль, нужно ≥ 70`);
  }
  if (!c.regime_ok) {
    reasons.push(`Режим рынка «${c.regime ?? "?"}» не подходит — нужно range или transition (то есть не сильный тренд)`);
  }
  if (!c.mtf_down_aligned) {
    reasons.push(
      `MTF тренд не вниз — сейчас ${c.mtf_direction ?? "?"} с согласием ${c.mtf_aligned_count ?? 0}/3 ТФ; нужно down И ≥ 2/3`,
    );
  }
  if (!c.bull_filter_ok) {
    reasons.push(`Рынок в bull-фазе — EMA50/EMA200 = ${(c.ema_ratio ?? 0).toFixed(3)} > 1.05`);
  }
  return reasons;
}

export default function Home() {
  const [conditions, setConditions] = useState<PaperConditions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const tick = async () => {
    try {
      const c = await fetchPaperConditions();
      setConditions(c);
      setLastUpdate(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    tick();
    const id = setInterval(tick, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const reasons = conditions ? whyBlocked(conditions) : [];

  return (
    <main className="p-6 md:p-10 max-w-5xl mx-auto flex flex-col gap-6">
      <header className="flex flex-col md:flex-row justify-between items-start md:items-end gap-4">
        <div>
          <h1 className="text-3xl md:text-4xl font-black tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-blue-400 via-cyan-300 to-emerald-400">
            ETH Options Assistant
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Бот следит за рынком и торгует на бумаге когда условия совпадают
          </p>
        </div>
        <div className="text-xs text-slate-400 font-mono flex items-center gap-3">
          <span className="relative inline-flex w-2 h-2">
            <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-60" />
            <span className="relative inline-block w-2 h-2 rounded-full bg-emerald-400" />
          </span>
          {lastUpdate ? `обновлено ${lastUpdate.toLocaleTimeString("ru-RU")}` : "ожидание данных…"}
        </div>
      </header>

      {error && (
        <div className="glass-panel p-4 border border-rose-500/40 text-rose-300 text-sm">
          Ошибка подключения к API: {error}
        </div>
      )}

      {!conditions && !error && (
        <div className="glass-panel p-8 text-center text-slate-400">Загрузка...</div>
      )}

      {conditions && (
        <>
          {/* Big banner — signal active or waiting */}
          <section
            className={`glass-panel p-6 border ${
              conditions.ready
                ? "border-emerald-500/50 bg-emerald-500/10"
                : "border-slate-700/40 bg-slate-800/20"
            }`}
          >
            <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
              <div className="flex items-center gap-3">
                {conditions.ready ? (
                  <>
                    <span className="relative inline-flex w-4 h-4">
                      <span className="absolute inset-0 rounded-full bg-emerald-400 animate-ping opacity-75" />
                      <span className="relative inline-block w-4 h-4 rounded-full bg-emerald-400" />
                    </span>
                    <span className="text-2xl font-bold text-emerald-200">🟢 ВХОД АКТУАЛЕН СЕЙЧАС</span>
                  </>
                ) : (
                  <>
                    <span className="inline-block w-4 h-4 rounded-full bg-slate-500" />
                    <span className="text-xl font-semibold text-slate-300">⏸ Ждём подходящих условий</span>
                  </>
                )}
              </div>
              <a
                href="/paper"
                className="px-5 py-2.5 rounded-lg bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 font-semibold hover:bg-emerald-500/30 text-center"
              >
                Открыть paper-dashboard →
              </a>
            </div>

            {conditions.spot !== null && (
              <div className="mt-4 text-xs text-slate-400 font-mono">
                ETH = ${conditions.spot.toFixed(2)} · последняя проверка{" "}
                {new Date(conditions.checked_at_ms).toLocaleTimeString("ru-RU")}
              </div>
            )}

            {!conditions.ready && reasons.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-xs uppercase tracking-wider text-slate-500">Почему не входим:</div>
                <ul className="space-y-1.5">
                  {reasons.map((r, i) => (
                    <li key={i} className="text-sm text-slate-300 flex gap-2">
                      <span className="text-rose-400">•</span>
                      <span>{r}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          {/* Per-condition pills */}
          <section className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <ConditionPill
              label="Высокая волатильность"
              ok={conditions.vol_high}
              detail={
                conditions.vol_pctile !== null
                  ? `${Math.round((conditions.vol_pctile || 0) * 100)}-й перцентиль`
                  : "—"
              }
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
              detail={
                conditions.ema_ratio !== null ? `EMA50/200 = ${conditions.ema_ratio.toFixed(3)}` : "—"
              }
              need="≤ 1.05"
            />
          </section>

          {/* Explanation */}
          <section className="glass-panel p-5 text-sm text-slate-400 leading-relaxed">
            <div className="text-slate-200 font-semibold mb-2">Как это работает</div>
            <p>
              Каждые 5 минут бот проверяет 4 условия выше. Когда ВСЕ четыре сходятся одновременно — продаёт ATM Call-опцион
              на 10% от баланса (мин $5, макс $50). Закрывает на тейк-профите −30/−50%, стоп-лоссе +50% или через 24h.
            </p>
            <p className="mt-3">
              В тихом рынке (как сейчас) сигналы редкие — могут быть 0-3 в день. После 3 убытков подряд бот делает паузу на сутки.
              Все сделки и текущий баланс — на странице{" "}
              <a href="/paper" className="text-emerald-300 hover:text-emerald-200 underline">
                /paper
              </a>
              .
            </p>
          </section>
        </>
      )}
    </main>
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
        ok ? "bg-emerald-500/10 border-emerald-500/30" : "bg-slate-700/30 border-slate-600/30"
      }`}
    >
      <div className="flex items-center gap-2">
        <span className={ok ? "text-emerald-400 text-base" : "text-slate-500 text-base"}>
          {ok ? "✓" : "✕"}
        </span>
        <span className="font-semibold text-slate-200 text-xs">{label}</span>
      </div>
      <div className="mt-1 text-slate-400 text-xs">{detail}</div>
      <div className="text-[10px] text-slate-500 mt-0.5">{need}</div>
    </div>
  );
}
