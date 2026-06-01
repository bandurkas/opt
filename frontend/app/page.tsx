"use client";

import { useEffect, useState } from "react";
import {
  fetchPaperConditions,
  fetchPaperPositions,
  fetchPaperState,
  type PaperConditions,
  type PaperPosition,
  type PaperState,
} from "./lib/api";
import { MissedSignals } from "./components/MissedSignals";

const REFRESH_MS = 15_000;

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtPct = (v: number | null, d = 2) =>
  v === null || v === undefined
    ? "—"
    : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`;
const fmtTime = (ms: number) =>
  new Date(ms).toISOString().slice(0, 16).replace("T", " ") + " UTC";

export default function Dashboard() {
  const [state, setState] = useState<PaperState | null>(null);
  const [conditions, setConditions] = useState<PaperConditions | null>(null);
  const [open, setOpen] = useState<PaperPosition[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    // State + positions poll every 15s
    const loadState = async () => {
      try {
        const [s, op] = await Promise.all([
          fetchPaperState(),
          fetchPaperPositions("open"),
        ]);
        if (cancelled) return;
        setState(s);
        setOpen(op.positions);
        setLastUpdate(new Date());
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    // Conditions poll every 60s — heavy endpoint (2100 5m bars + indicators)
    const loadConditions = async () => {
      try {
        const c = await fetchPaperConditions();
        if (cancelled) return;
        setConditions(c);
      } catch {
        // Non-critical — badge will show default
      }
    };
    loadState();
    loadConditions();
    const id1 = setInterval(loadState, REFRESH_MS);
    const id2 = setInterval(loadConditions, 60_000);
    return () => {
      cancelled = true;
      clearInterval(id1);
      clearInterval(id2);
    };
  }, []);

  return (
    <main className="max-w-6xl mx-auto p-4 md:p-8 flex flex-col gap-6">
      <header className="flex flex-col md:flex-row md:items-end md:justify-between gap-2">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold text-slate-100">
            ETH Options Assistant
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            V3 Hybrid · 7d-return switching · Put/Call · CB=5/48h
          </p>
        </div>
        <div className="text-xs text-slate-500 font-mono">
          {lastUpdate
            ? `обновлено ${lastUpdate.toLocaleTimeString("ru-RU")}`
            : "ожидание данных…"}
        </div>
      </header>

      {error && (
        <div className="glass-panel p-4 border border-rose-500/40 text-rose-300 text-sm">
          Ошибка API: {error}
        </div>
      )}

      {state && <LiveState state={state} conditions={conditions} />}
      {open.length > 0 && <OpenPositions positions={open} />}

      <MissedSignals />
    </main>
  );
}

function LiveState({ state, conditions }: { state: PaperState; conditions: PaperConditions | null }) {
  const change = state.current_equity_usd - state.start_equity_usd;
  const changePct = (change / state.start_equity_usd) * 100;
  const isUp = change >= 0;
  const lastSig = state.last_signal_age_h;
  const sigLabel =
    lastSig === null
      ? "нет за окно"
      : lastSig < 1
        ? `${Math.round(lastSig * 60)} мин назад`
        : lastSig < 24
          ? `${lastSig.toFixed(1)}h назад`
          : `${(lastSig / 24).toFixed(1)}d назад`;

  // Active side from conditions endpoint (where it actually lives)
  const activeSide = conditions?.active_side || "P";
  const ret7d = conditions?.ret_7d;

  return (
    <section className="glass-panel p-6 grid grid-cols-2 md:grid-cols-4 gap-4">
      {/* Active Side Badge */}
      <Cell
        label="Активная сторона"
        value={
          <span className={activeSide === "P" ? "text-rose-300" : "text-emerald-300"}>
            SELL {activeSide === "P" ? "PUT" : "CALL"}
          </span>
        }
        sub={
          ret7d !== undefined
            ? `7d ret: ${ret7d > 0 ? "+" : ""}${ret7d?.toFixed?.(2) ?? ret7d}%`
            : ""
        }
      />
      <Cell
        label="Equity"
        value={fmtUsd(state.current_equity_usd)}
        sub={`${isUp ? "+" : ""}${fmtUsd(change)} · ${fmtPct(changePct)}`}
        accent={isUp ? "text-emerald-300" : "text-rose-300"}
      />
      <Cell
        label="Сделок"
        value={`${state.n_closed} закрыто · ${state.n_open} открыто`}
        sub={
          state.win_rate !== null
            ? `WR ${(state.win_rate * 100).toFixed(1)}% · ${state.wins}W/${state.losses}L`
            : "—"
        }
      />
      <Cell
        label="Avg P&L"
        value={state.n_closed > 0 ? fmtPct(state.avg_pnl_pct) : "—"}
        sub={`realized ${fmtUsd(state.realized_usd)}`}
      />
      {state.cb_active && (
        <div className="col-span-2 md:col-span-4 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs">
          ⏸ Circuit breaker активен — пауза 48h после 5 убытков подряд.
        </div>
      )}
    </section>
  );
}

function Cell({
  label,
  value,
  sub,
  accent = "text-slate-100",
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  accent?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-bold">
        {label}
      </div>
      <div className={`text-xl font-bold font-mono mt-1 ${accent}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function OpenPositions({ positions }: { positions: PaperPosition[] }) {
  return (
    <section className="glass-panel p-6">
      <div className="text-[11px] uppercase tracking-widest text-slate-500 font-bold mb-3">
        Открытые позиции ({positions.length})
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-700/50">
        <table className="w-full text-xs">
          <thead className="bg-slate-800/50 text-slate-400">
            <tr>
              <th className="text-left p-2">Открыто</th>
              <th className="text-left p-2">Сторона</th>
              <th className="text-right p-2">Strike</th>
              <th className="text-right p-2">Размер</th>
              <th className="text-right p-2">Премия</th>
              <th className="text-right p-2">Экспирация</th>
              <th className="text-left p-2">Статус</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.id} className="border-t border-slate-800/50">
                <td className="p-2 font-mono text-slate-300">
                  {fmtTime(p.opened_at_ms)}
                </td>
                <td className="p-2">
                  <span
                    className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      p.side === "P"
                        ? "bg-rose-500/10 text-rose-300"
                        : "bg-emerald-500/10 text-emerald-300"
                    }`}
                  >
                    SELL {p.side === "P" ? "PUT" : "CALL"}
                  </span>
                </td>
                <td className="p-2 text-right font-mono">${p.strike}</td>
                <td className="p-2 text-right font-mono text-slate-300">
                  {p.contracts.toFixed(2)} ETH
                </td>
                <td className="p-2 text-right font-mono text-slate-300">
                  {fmtUsd(p.entry_credit_usd)}
                </td>
                <td className="p-2 text-right font-mono text-slate-400">
                  {fmtTime(p.expiry_ms)}
                </td>
                <td className="p-2 text-[10px] uppercase text-slate-400">
                  {p.status}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
