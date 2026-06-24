"use client";

import { useEffect, useState } from "react";
import { fetchPaperState, fetchPaperConditions, fetchPaperPositions, fetchRecentTrades, fetchEquityHistory, fetchBtcStraddleState, fetchBtcStraddlePositions, fetchBtcStraddleEquityHistory, fetchEthStraddleState, fetchEthStraddlePositions, fetchEthStraddleEquityHistory, fetchEthStraddleChart, type PaperState, type PaperConditions, type PaperPosition, type EquityPoint, type BtcStraddleState, type BtcStraddlePosition, type EthStraddleState, type EthStraddlePosition, type Kline, type EthStraddleChartLeg } from "./lib/api";
import MissionControl from "./components/MissionControl";
import StraddleChart from "./components/StraddleChart";

const REFRESH_MS = 15_000;

const fmtUsd = (v: number, d = 2) => `$${v.toFixed(d)}`;
const fmtPct = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
const fmtTime = (ms: number) => {
  const d = new Date(ms);
  const now = Date.now();
  const diff = now - ms;
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return d.toLocaleDateString();
};
const fmtRemaining = (hold_h: number, opened_ms: number) => {
  const elapsed_h = (Date.now() - opened_ms) / 3600000;
  const remaining = hold_h - elapsed_h;
  if (remaining <= 0) return "closing soon";
  if (remaining < 24) return `${remaining.toFixed(1)}h left`;
  return `${(remaining / 24).toFixed(1)}d left`;
};
const fmtDay = (ms: number) => {
  const d = new Date(ms);
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return `${days[d.getDay()]} ${d.getDate()}`;
};
// Traffic-light dot: red = not met, yellow = met but waiting on others, green = met & all ready.
const condDotColor = (met: boolean, ready: boolean) =>
  !met ? "bg-rose-500" : ready ? "bg-emerald-500" : "bg-amber-400";

export default function Dashboard() {
  const [state, setState] = useState<PaperState | null>(null);
  const [conditions, setConditions] = useState<PaperConditions | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [recentTrades, setRecentTrades] = useState<PaperPosition[]>([]);
  const [equityHistory, setEquityHistory] = useState<EquityPoint[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);

  const [btcState, setBtcState] = useState<BtcStraddleState | null>(null);
  const [btcPositions, setBtcPositions] = useState<BtcStraddlePosition[]>([]);
  const [btcRecentTrades, setBtcRecentTrades] = useState<BtcStraddlePosition[]>([]);
  const [btcEquityHistory, setBtcEquityHistory] = useState<EquityPoint[]>([]);
  const [btcError, setBtcError] = useState<string | null>(null);

  const [ethStraddleState, setEthStraddleState] = useState<EthStraddleState | null>(null);
  const [ethStraddlePositions, setEthStraddlePositions] = useState<EthStraddlePosition[]>([]);
  const [ethStraddleRecentTrades, setEthStraddleRecentTrades] = useState<EthStraddlePosition[]>([]);
  const [ethStraddleEquityHistory, setEthStraddleEquityHistory] = useState<EquityPoint[]>([]);
  const [ethStraddleError, setEthStraddleError] = useState<string | null>(null);
  const [ethStraddleKlines, setEthStraddleKlines] = useState<Kline[]>([]);
  const [ethStraddleChartLegs, setEthStraddleChartLegs] = useState<EthStraddleChartLeg[]>([]);

  // Separate effect/error state from the ETH signal book above — the BTC bot is a
  // distinct deploy (own container/tables) and may lag behind or be absent;
  // its fetch failures must never blank out the ETH dashboard.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, p, t, eq] = await Promise.all([
          fetchBtcStraddleState(),
          fetchBtcStraddlePositions("open"),
          fetchBtcStraddlePositions("recent", 200),
          fetchBtcStraddleEquityHistory(336),
        ]);
        if (cancelled) return;
        setBtcState(s);
        setBtcPositions(p.positions);
        setBtcRecentTrades(t.positions.filter((pos) => pos.closed_at_ms !== null));
        setBtcEquityHistory(eq.points);
        setBtcError(null);
      } catch (e) {
        if (cancelled) return;
        setBtcError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Same isolation as the BTC straddle effect above — the ETH straddle bot is
  // a distinct deploy (own container/tables) from both the ETH signal book and
  // the BTC straddle book; its fetch failures must never blank out either.
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, p, t, eq, chart] = await Promise.all([
          fetchEthStraddleState(),
          fetchEthStraddlePositions("open"),
          fetchEthStraddlePositions("recent", 200),
          fetchEthStraddleEquityHistory(336),
          fetchEthStraddleChart(),
        ]);
        if (cancelled) return;
        setEthStraddleState(s);
        setEthStraddlePositions(p.positions);
        setEthStraddleRecentTrades(t.positions.filter((pos) => pos.closed_at_ms !== null));
        setEthStraddleEquityHistory(eq.points);
        setEthStraddleKlines(chart.klines);
        setEthStraddleChartLegs(chart.legs);
        setEthStraddleError(null);
      } catch (e) {
        if (cancelled) return;
        setEthStraddleError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [s, c, p, t, eq] = await Promise.all([
          fetchPaperState(),
          fetchPaperConditions(),
          fetchPaperPositions("open"),
          fetchRecentTrades(200),
          fetchEquityHistory(336), // 14 days
        ]);
        if (cancelled) return;
        setState(s);
        setConditions(c);
        setPositions(p.positions);
        setRecentTrades(t.positions.filter((pos: PaperPosition) => pos.closed_at_ms !== null));
        setEquityHistory(eq.points);
        setLastUpdate(new Date());
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  if (!state) return <main className="min-h-screen bg-slate-950 text-white flex items-center justify-center">Loading...</main>;

  const ret7d = conditions?.ret_7d ?? 0;
  const activeSide = conditions?.active_side;
  const deadZone = conditions?.dead_zone ?? false;
  const retPutMax = conditions?.thresholds?.ret_threshold_put ?? -2.5;
  const retCallMin = conditions?.thresholds?.ret_threshold_call ?? 1.0;

  let distToSignal = "";
  if (deadZone) {
    if (ret7d < 0) {
      const drop = Math.abs(retPutMax - ret7d);
      distToSignal = `${drop.toFixed(2)}% more drop to sell Put`;
    } else {
      const rise = Math.abs(retCallMin - ret7d);
      distToSignal = `${rise.toFixed(2)}% more rise to sell Call`;
    }
  } else if (activeSide === "P") {
    distToSignal = `Conditions met for Put`;
  } else if (activeSide === "C") {
    distToSignal = `Conditions met for Call`;
  }

  const change = state.current_equity_usd - state.start_equity_usd;
  const isUp = change >= 0;

  // Last 24h stats
  const last24h = recentTrades.filter(t => t.closed_at_ms && (Date.now() - t.closed_at_ms) < 86400000);
  const last24hPnl = last24h.reduce((sum, t) => sum + (t.pnl_usd || 0), 0);

  // Entry-condition checklist (traffic-light dots)
  const ready = conditions?.ready ?? false;
  const entryConds = conditions ? [
    { label: "Сторона выбрана", met: conditions.active_side !== null,
      value: conditions.active_side === "P" ? "Put" : conditions.active_side === "C" ? "Call" : "нет (флэт)" },
    { label: "Волатильность", met: conditions.vol_high,
      value: conditions.vol_pctile != null ? `${(conditions.vol_pctile * 100).toFixed(0)}%-ile` : "—" },
    { label: "Режим (ADX)", met: conditions.regime_ok,
      value: conditions.regime ?? "—" },
    { label: "MTF тренд", met: conditions.mtf_direction_ok,
      value: `${conditions.mtf_direction ?? "—"} ${conditions.mtf_aligned_count ?? 0}/3` },
    ...(conditions.active_side === "P" ? [{
      label: "Bull-фильтр", met: conditions.bull_filter_ok,
      value: conditions.ema_ratio != null ? conditions.ema_ratio.toFixed(3) : "—",
    }] : []),
  ] : [];

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* Header */}
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">ETH Options</h1>
            <p className="text-xs text-slate-500">V2 hybrid · 7d switching · Paper $400</p>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">{lastUpdate?.toLocaleTimeString("ru-RU")}</p>
            {error && <p className="text-xs text-rose-400">{error}</p>}
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto p-4 space-y-4">
        <MissionControl />
        {/* Active Side Banner */}
        <div className={`rounded-xl p-4 border ${
          deadZone ? "bg-slate-900 border-slate-700"
            : activeSide === "P" ? "bg-rose-950/30 border-rose-800/50"
            : "bg-emerald-950/30 border-emerald-800/50"
        }`}>
          <div className="flex items-center justify-between">
            <div>
              {deadZone ? (
                <>
                  <p className="text-slate-400 text-sm">⏸ Dead Zone</p>
                  <p className="text-xs text-slate-500 mt-1">{distToSignal}</p>
                </>
              ) : (
                <>
                  <p className={`text-lg font-bold ${activeSide === "P" ? "text-rose-300" : "text-emerald-300"}`}>
                    SELL {activeSide === "P" ? "PUT" : "CALL"}
                  </p>
                  <p className="text-xs text-slate-400 mt-1">{distToSignal}</p>
                </>
              )}
            </div>
            <div className="text-right">
              <p className="text-2xl font-mono font-bold">{ret7d > 0 ? "+" : ""}{ret7d.toFixed(2)}%</p>
              <p className="text-xs text-slate-500">7d return</p>
            </div>
          </div>
          {deadZone && (
            <div className="mt-3">
              <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden relative">
                <div className="absolute left-0 top-0 bottom-0 w-px bg-rose-600" />
                <div className="absolute right-0 top-0 bottom-0 w-px bg-emerald-600" />
                <div className="absolute top-0 bottom-0 w-2 bg-white rounded-full transition-all duration-500"
                  style={{ left: `${Math.max(0, Math.min(100, ((ret7d - retPutMax) / (retCallMin - retPutMax)) * 100))}%` }} />
              </div>
              <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                <span>Put &lt;{retPutMax}%</span>
                <span>Dead Zone</span>
                <span>Call &gt;{retCallMin}%</span>
              </div>
            </div>
          )}
        </div>

        {/* Entry-proximity gauge — how close the market is to a tradeable entry */}
        {conditions?.proximity && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between items-center">
              <span>Близость ко входу</span>
              {conditions.adx && (
                <span className="font-mono text-[10px] text-slate-500">
                  ADX {conditions.adx.adx?.toFixed(0) ?? "—"} · score {conditions.adx.score.toFixed(1)}/10
                </span>
              )}
            </div>
            <div className="p-4">
              <ProximityGauge pct={conditions.proximity.proximity_pct} zone={conditions.proximity.zone} />
              <div className="grid grid-cols-3 gap-2 mt-3">
                {([["MTF", "mtf"], ["Vol", "vol"], ["Bull", "bull"]] as const).map(([lbl, k]) => (
                  <FactorBar key={k} label={lbl} v={conditions.proximity!.factors[k]} />
                ))}
              </div>
              <p className="text-[10px] text-slate-600 mt-3 text-center">
                100% = все условия входа выполнены. ADX-скор — индикатор силы сигнала, не размер позиции.
              </p>
            </div>
          </div>
        )}

        {/* Entry conditions — traffic-light dots */}
        {conditions && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between items-center">
              <span>Условия входа</span>
              <span className={`flex items-center gap-1.5 ${ready ? "text-emerald-400" : "text-slate-500"}`}>
                <span className={`inline-block w-2 h-2 rounded-full ${ready ? "bg-emerald-500" : "bg-amber-400"}`} />
                {ready ? "вход в сделку" : "ожидание"}
              </span>
            </div>
            <div className="divide-y divide-slate-800">
              {entryConds.map((c) => (
                <div key={c.label} className="px-4 py-2.5 flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2.5">
                    <span className={`inline-block w-2.5 h-2.5 rounded-full ${condDotColor(c.met, ready)}`} />
                    <span className="text-slate-300">{c.label}</span>
                  </div>
                  <span className="font-mono text-xs text-slate-500">{c.value}</span>
                </div>
              ))}
            </div>
            <div className="px-4 py-2 flex flex-wrap gap-3 text-[10px] text-slate-500 border-t border-slate-800">
              <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-rose-500" />не выполнено</span>
              <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-amber-400" />ждём остальные</span>
              <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />вход</span>
            </div>
          </div>
        )}

        {/* Stats Row */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Equity" value={fmtUsd(state.current_equity_usd)} sub={`${isUp ? "+" : ""}${fmtUsd(change)} (${fmtPct((change / state.start_equity_usd) * 100)})`} accent={isUp ? "text-emerald-300" : "text-rose-300"} />
          <StatCard label="Win Rate" value={state.win_rate ? `${(state.win_rate * 100).toFixed(0)}%` : "—"} sub={`${state.wins}W / ${state.losses}L`} />
          <StatCard label="Trades" value={`${state.n_closed}`} sub={`${state.n_open} open`} />
          <StatCard label="24h PnL" value={fmtUsd(last24hPnl)} sub={`${last24h.length} trades`} accent={last24hPnl >= 0 ? "text-emerald-300" : "text-rose-300"} />
        </div>

        {/* Circuit Breaker */}
        {state.cb_active && (
          <div className="bg-amber-950/30 border border-amber-800/50 rounded-xl px-4 py-3 text-sm text-amber-300">
            ⏸ Circuit breaker active · {state.consec_losses} losses · pause 48h
          </div>
        )}

        {/* Equity Chart */}
        {equityHistory.length > 1 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
              Equity (14 days)
            </div>
            <div className="p-2">
              <EquityChart points={equityHistory} startEquity={state.start_equity_usd} />
            </div>
          </div>
        )}

        {/* Open Positions */}
        {positions.length > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
              Open Positions ({positions.length})
            </div>
            <div className="divide-y divide-slate-800">
              {positions.map((p) => (
                <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                  <div>
                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      p.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                    }`}>
                      SELL {p.side}
                    </span>
                    <span className="ml-2 text-sm font-mono">${p.strike}</span>
                    <span className="ml-2 text-xs text-slate-500">{p.contracts.toFixed(2)} ETH</span>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-slate-500">{fmtRemaining(p.hold_h, p.opened_at_ms)}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Trade Journal — real executed trades, grows over time */}
        {recentTrades.length > 0 && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
            <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
              <span>Журнал сделок</span>
              <span>{recentTrades.length} total</span>
            </div>
            <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
              {recentTrades.map((t) => {
                const isWin = (t.pnl_usd || 0) > 0;
                return (
                  <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                        t.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                      }`}>
                        {t.side}
                      </span>
                      <span className="font-mono text-xs">${t.strike}</span>
                      <span className="text-xs text-slate-500">{t.closed_at_ms ? fmtDay(t.closed_at_ms) : ""}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                      <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                        {fmtPct(t.pnl_pct || 0)}
                      </span>
                      <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                        {t.pnl_usd != null ? fmtUsd(t.pnl_usd) : ""}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* No positions + no trades */}
        {positions.length === 0 && recentTrades.length === 0 && !state.cb_active && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
            <p className="text-sm text-slate-400">No activity yet</p>
            <p className="text-xs text-slate-500 mt-1">Waiting for first signal...</p>
          </div>
        )}

        {/* ───────────────────── BTC Straddle (separate book) ───────────────────── */}
        <div className="pt-2">
          <h2 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-3">
            BTC Straddle <span className="text-slate-600 font-normal">· 24h unconditional short ATM</span>
          </h2>
        </div>

        {btcError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            BTC straddle bot unreachable: {btcError}
          </div>
        )}

        {btcState && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(btcState.current_equity_usd)}
                sub={`${(btcState.current_equity_usd - btcState.start_equity_usd) >= 0 ? "+" : ""}${fmtUsd(btcState.current_equity_usd - btcState.start_equity_usd)}`}
                accent={btcState.current_equity_usd >= btcState.start_equity_usd ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard label="Win Rate" value={btcState.win_rate ? `${(btcState.win_rate * 100).toFixed(0)}%` : "—"} sub={`${btcState.wins}W / ${btcState.losses}L`} />
              <StatCard label="Legs closed" value={`${btcState.n_closed}`} sub={`${btcState.n_open} open`} />
              <StatCard label="Max DD" value={`${btcState.max_dd_pct.toFixed(1)}%`} sub={`cycle #${btcState.last_cycle_id}`} />
            </div>

            {btcEquityHistory.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Equity (14 days)
                </div>
                <div className="p-2">
                  <EquityChart points={btcEquityHistory} startEquity={btcState.start_equity_usd} />
                </div>
              </div>
            )}

            {btcPositions.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Open Legs ({btcPositions.length})
                </div>
                <div className="divide-y divide-slate-800">
                  {btcPositions.map((p) => (
                    <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                      <div>
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                        }`}>
                          SELL {p.leg}
                        </span>
                        <span className="ml-2 text-sm font-mono">${p.strike}</span>
                        <span className="ml-2 text-xs text-slate-500">{p.contracts.toFixed(4)} BTC</span>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-slate-500">cycle #{p.cycle_id}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {btcRecentTrades.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал циклов</span>
                  <span>{btcRecentTrades.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {btcRecentTrades.map((t) => {
                    const isWin = (t.pnl_usd || 0) > 0;
                    return (
                      <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {t.leg}
                          </span>
                          <span className="font-mono text-xs">${t.strike}</span>
                          <span className="text-xs text-slate-500">{t.closed_at_ms ? fmtDay(t.closed_at_ms) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtPct(t.pnl_pct || 0)}
                          </span>
                          <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_usd != null ? fmtUsd(t.pnl_usd) : ""}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {btcPositions.length === 0 && btcRecentTrades.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Next cycle opens at the 24h boundary...</p>
              </div>
            )}
          </>
        )}

        {/* ───────────────────── ETH Straddle (separate book) ───────────────────── */}
        <div className="pt-2">
          <h2 className="text-sm font-bold text-slate-400 uppercase tracking-widest mb-3">
            ETH Straddle <span className="text-slate-600 font-normal">· 24h unconditional short ATM</span>
          </h2>
        </div>

        {ethStraddleError && (
          <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
            ETH straddle bot unreachable: {ethStraddleError}
          </div>
        )}

        {ethStraddleState && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatCard
                label="Equity"
                value={fmtUsd(ethStraddleState.current_equity_usd)}
                sub={`${(ethStraddleState.current_equity_usd - ethStraddleState.start_equity_usd) >= 0 ? "+" : ""}${fmtUsd(ethStraddleState.current_equity_usd - ethStraddleState.start_equity_usd)}`}
                accent={ethStraddleState.current_equity_usd >= ethStraddleState.start_equity_usd ? "text-emerald-300" : "text-rose-300"}
              />
              <StatCard label="Win Rate" value={ethStraddleState.win_rate ? `${(ethStraddleState.win_rate * 100).toFixed(0)}%` : "—"} sub={`${ethStraddleState.wins}W / ${ethStraddleState.losses}L`} />
              <StatCard label="Legs closed" value={`${ethStraddleState.n_closed}`} sub={`${ethStraddleState.n_open} open`} />
              <StatCard label="Max DD" value={`${ethStraddleState.max_dd_pct.toFixed(1)}%`} sub={`cycle #${ethStraddleState.last_cycle_id}`} />
            </div>

            {ethStraddleEquityHistory.length > 1 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Equity (14 days)
                </div>
                <div className="p-2">
                  <EquityChart points={ethStraddleEquityHistory} startEquity={ethStraddleState.start_equity_usd} />
                </div>
              </div>
            )}

            {ethStraddlePositions.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400">
                  Open Legs ({ethStraddlePositions.length})
                </div>
                <div className="divide-y divide-slate-800">
                  {ethStraddlePositions.map((p) => (
                    <div key={p.id} className="px-4 py-3 flex items-center justify-between">
                      <div>
                        <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          p.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                        }`}>
                          SELL {p.leg}
                        </span>
                        <span className="ml-2 text-sm font-mono">${p.strike}</span>
                        <span className="ml-2 text-xs text-slate-500">{p.contracts.toFixed(4)} ETH</span>
                      </div>
                      <div className="text-right">
                        <p className="text-xs text-slate-500">cycle #{p.cycle_id}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {ethStraddleKlines.length > 1 && (
              <StraddleChart
                callsign="GROGU-1"
                symbol="ETH"
                klines={ethStraddleKlines}
                legs={ethStraddleChartLegs}
              />
            )}

            {ethStraddleRecentTrades.length > 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between">
                  <span>Журнал циклов</span>
                  <span>{ethStraddleRecentTrades.length} total</span>
                </div>
                <div className="divide-y divide-slate-800 max-h-80 overflow-y-auto">
                  {ethStraddleRecentTrades.map((t) => {
                    const isWin = (t.pnl_usd || 0) > 0;
                    return (
                      <div key={t.id} className="px-4 py-2.5 flex items-center justify-between text-sm">
                        <div className="flex items-center gap-2">
                          <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            t.leg === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
                          }`}>
                            {t.leg}
                          </span>
                          <span className="font-mono text-xs">${t.strike}</span>
                          <span className="text-xs text-slate-500">{t.closed_at_ms ? fmtDay(t.closed_at_ms) : ""}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-slate-500">{t.exit_reason || ""}</span>
                          <span className={`font-mono font-bold text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {fmtPct(t.pnl_pct || 0)}
                          </span>
                          <span className={`font-mono text-xs ${isWin ? "text-emerald-400" : "text-rose-400"}`}>
                            {t.pnl_usd != null ? fmtUsd(t.pnl_usd) : ""}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {ethStraddlePositions.length === 0 && ethStraddleRecentTrades.length === 0 && (
              <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-6 text-center">
                <p className="text-sm text-slate-400">No activity yet</p>
                <p className="text-xs text-slate-500 mt-1">Next cycle opens at the 24h boundary...</p>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}

const ZONE_LABEL: Record<string, string> = {
  waiting: "Ожидание", preparing: "Подготовка", ready: "Готовность", entry: "Вход!",
};
const ZONE_COLOR: Record<string, string> = {
  waiting: "#f43f5e", preparing: "#f59e0b", ready: "#10b981", entry: "#10b981",
};

function ProximityGauge({ pct, zone }: { pct: number; zone: string }) {
  const cx = 100, cy = 100, r = 80;
  const clamped = Math.max(0, Math.min(100, pct));
  const color = ZONE_COLOR[zone] ?? "#64748b";

  // 0% → 180° (left), 100% → 0° (right); top semicircle (screen y is down).
  const polar = (deg: number, rad: number): [number, number] => {
    const a = (deg * Math.PI) / 180;
    return [cx + rad * Math.cos(a), cy - rad * Math.sin(a)];
  };
  // Sample [a,b] of the scale as a polyline (unambiguous vs SVG arc flags).
  const scalePoints = (a: number, b: number) => {
    const pts: string[] = [];
    const steps = Math.max(2, Math.round(Math.abs(b - a) / 2));
    for (let i = 0; i <= steps; i++) {
      const p = a + ((b - a) * i) / steps;
      const [x, y] = polar(180 - p * 1.8, r);
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
    }
    return pts.join(" ");
  };

  const angle = (clamped / 100) * 180 - 90; // needle rotation: -90° left, 0° up, +90° right
  const needleLen = r - 14;

  return (
    <div className="flex flex-col items-center">
      {/* viewBox cropped to the arc; readout lives BELOW it so the needle never overlaps text */}
      <svg viewBox="0 0 200 108" className="w-full max-w-[17rem] mx-auto">
        {/* base track for depth */}
        <polyline points={scalePoints(0, 100)} fill="none" stroke="#1e293b" strokeWidth="13" strokeLinecap="round" />
        {/* dim coloured zones */}
        <polyline points={scalePoints(0, 50)} fill="none" stroke="#f43f5e" strokeWidth="11" opacity="0.25" strokeLinecap="round" />
        <polyline points={scalePoints(50, 80)} fill="none" stroke="#f59e0b" strokeWidth="11" opacity="0.25" />
        <polyline points={scalePoints(80, 100)} fill="none" stroke="#10b981" strokeWidth="11" opacity="0.25" strokeLinecap="round" />
        {/* bright value fill 0 → pct */}
        {clamped > 0 && (
          <polyline points={scalePoints(0, clamped)} fill="none" stroke={color} strokeWidth="11" strokeLinecap="round" />
        )}
        {/* needle */}
        <g transform={`rotate(${angle} ${cx} ${cy})`}>
          <polygon
            points={`${cx - 3.2},${cy} ${cx + 3.2},${cy} ${cx},${cy - needleLen}`}
            fill={color}
          />
        </g>
        {/* hub bearing */}
        <circle cx={cx} cy={cy} r="6.5" fill={color} />
        <circle cx={cx} cy={cy} r="3" fill="#0f172a" />
      </svg>
      {/* readout — clear of the needle sweep */}
      <div className="flex flex-col items-center -mt-1">
        <div className="flex items-baseline gap-0.5 leading-none">
          <span className="text-4xl font-bold tabular-nums" style={{ color }}>{clamped.toFixed(0)}</span>
          <span className="text-lg font-semibold" style={{ color }}>%</span>
        </div>
        <span className="mt-1 text-[11px] font-medium uppercase tracking-[0.15em]" style={{ color }}>
          {ZONE_LABEL[zone] ?? zone}
        </span>
      </div>
    </div>
  );
}

function FactorBar({ label, v }: { label: string; v: number }) {
  const pct = Math.max(0, Math.min(100, v * 100));
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-400" : "bg-rose-500";
  return (
    <div>
      <div className="flex justify-between text-[10px] text-slate-500 mb-0.5">
        <span>{label}</span><span className="font-mono">{pct.toFixed(0)}</span>
      </div>
      <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function StatCard({ label, value, sub, accent }: { label: string; value: React.ReactNode; sub?: string; accent?: string }) {
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl px-4 py-3">
      <p className="text-[10px] uppercase tracking-widest text-slate-500">{label}</p>
      <p className={`text-xl font-bold font-mono mt-1 ${accent ?? "text-slate-100"}`}>{value}</p>
      {sub && <p className="text-[11px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function EquityChart({ points, startEquity }: { points: EquityPoint[]; startEquity: number }) {
  if (points.length < 2) return null;

  const w = 800, h = 120, pad = 4;
  const minEq = Math.min(...points.map(p => p.equity), startEquity);
  const maxEq = Math.max(...points.map(p => p.equity), startEquity);
  const range = maxEq - minEq || 1;

  const toX = (i: number) => pad + (i / (points.length - 1)) * (w - pad * 2);
  const toY = (v: number) => h - pad - ((v - minEq) / range) * (h - pad * 2);

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"} ${toX(i)} ${toY(p.equity)}`).join(" ");
  const areaPath = linePath + ` L ${toX(points.length - 1)} ${h} L ${toX(0)} ${h} Z`;

  const isProfit = points[points.length - 1].equity >= startEquity;
  const lineColor = isProfit ? "#10b981" : "#f43f5e";
  const fillColor = isProfit ? "rgba(16,185,129,0.1)" : "rgba(244,63,94,0.1)";

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-28" preserveAspectRatio="none">
      {/* Start line */}
      <line x1={toX(0)} y1={toY(startEquity)} x2={toX(points.length - 1)} y2={toY(startEquity)} stroke="#334155" strokeWidth="1" strokeDasharray="4 4" />
      {/* Area */}
      <path d={areaPath} fill={fillColor} />
      {/* Line */}
      <path d={linePath} fill="none" stroke={lineColor} strokeWidth="2" />
      {/* Current value */}
      <text x={toX(points.length - 1)} y={toY(points[points.length - 1].equity) - 6} fill={lineColor} fontSize="11" fontWeight="bold" textAnchor="end">
        {fmtUsd(points[points.length - 1].equity)}
      </text>
    </svg>
  );
}
