"use client";

import { useEffect, useMemo, useState } from "react";
import { BOT_META } from "./MissionControl";
import type { BotName } from "../lib/api";

// Bot identity (callsign, accent color) is sourced from MissionControl's
// BOT_META — the single source of truth — so a contract chip and its bot's
// control panel never drift out of sync. Only the ring/dot tones (chip-only
// styling MissionControl has no use for) are kept here.
// Tyagach is excluded from BotName/BOT_META (it has its own pause/resume/
// close-all API, not the shared control endpoint MissionControl drives —
// see MissionControl's separate confirmTarget union), so it's added as a
// one-off entry below rather than via BOT_META's Object.keys() mapping.
export type BotKey = BotName | "tyagach" | "jony";

const RING_DOT: Record<BotKey, { ring: string; dot: string }> = {
  btc_straddle: { ring: "ring-orange-500/40", dot: "bg-orange-400" },
  eth_straddle: { ring: "ring-cyan-400/40", dot: "bg-cyan-400" },
  eth_signal: { ring: "ring-fuchsia-400/40", dot: "bg-fuchsia-400" },
  tyagach: { ring: "ring-lime-400/40", dot: "bg-lime-400" },
  jony: { ring: "ring-sky-400/40", dot: "bg-sky-400" },
};

const UNIT: Record<BotKey, string> = {
  btc_straddle: "BTC", eth_straddle: "ETH", eth_signal: "ETH", tyagach: "ETH",
  jony: "ETH+BTC",
};

export const BOT_DISPLAY: Record<BotKey, { callsign: string; unit: string; accent: string; ring: string; dot: string }> = {
  ...(Object.fromEntries(
    (Object.keys(BOT_META) as BotName[]).map((k) => [
      k,
      { callsign: BOT_META[k].callsign, unit: UNIT[k], accent: BOT_META[k].accent, ...RING_DOT[k] },
    ]),
  ) as Record<BotName, { callsign: string; unit: string; accent: string; ring: string; dot: string }>),
  tyagach: { callsign: "TYAGACH", unit: UNIT.tyagach, accent: "text-lime-400", ...RING_DOT.tyagach },
  // Jony is the same "separate service" case as Tyagach: own API (:8200),
  // own pause control — not driven by MissionControl's shared endpoint.
  jony: { callsign: "JONY", unit: UNIT.jony, accent: "text-sky-400", ...RING_DOT.jony },
};

// A short option position, normalized across the 3 bots (paper signal trades
// use "side", straddle legs use "leg" — both collapse to this shape).
export type Contract = {
  key: string;
  bot: BotKey;
  side: "C" | "P";
  strike: number;
  expiryMs: number;
  contracts: number;
  spot: number | null;
  entryCreditUsd?: number | null;
  currentMarkUsd?: number | null;
  unrealizedPnlUsd?: number | null;
  openedAtMs?: number | null;
  cycleId?: number | null;
};

// These bots are short-premium sellers: OTM-at-expiry is the WIN condition
// (premium decays to zero), ITM is the loss/assignment risk. Color coding
// below is inverted relative to a typical "ITM = highlighted" options UI —
// here OTM is the safe/green state and ITM is the amber/red warning state.
export function itmInfo(side: "C" | "P", strike: number, spot: number | null) {
  // bybit_client.get_spot_price() returns 0.0 (not null/an error) on a Bybit
  // outage — treat a non-positive spot as "no data", not a real $0 price, or
  // an outage would flash every short put as falsely ITM (and every call as
  // falsely deep OTM).
  if (spot == null || spot <= 0) return null;
  const itm = side === "C" ? spot > strike : spot < strike;
  const distanceUsd = Math.abs(spot - strike);
  const distancePct = strike > 0 ? (distanceUsd / strike) * 100 : 0;
  return { itm, distanceUsd, distancePct };
}

export function useLiveNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function formatCountdown(msLeft: number): string {
  if (msLeft <= 0) return "EXPIRED";
  const totalSec = Math.floor(msLeft / 1000);
  const d = Math.floor(totalSec / 86400);
  const h = Math.floor((totalSec % 86400) / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  if (d > 0) return `${d}д ${pad(h)}:${pad(m)}:${pad(s)}`;
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

// Visual urgency tier for the expiry countdown — final hour gets a pulsing
// warning (theta/gamma risk spikes as expiry nears for a short seller).
function urgencyColor(msLeft: number): string {
  if (msLeft <= 0) return "text-slate-500";
  if (msLeft < 3600_000) return "text-rose-400";
  if (msLeft < 6 * 3600_000) return "text-amber-400";
  return "text-slate-200";
}

export function Countdown({ expiryMs, now }: { expiryMs: number; now: number }) {
  const msLeft = expiryMs - now;
  const urgent = msLeft > 0 && msLeft < 3600_000;
  return (
    <span className={`font-mono tabular-nums text-sm font-bold ${urgencyColor(msLeft)} ${urgent ? "animate-pulse" : ""}`}>
      {formatCountdown(msLeft)}
    </span>
  );
}

export function ItmBadge({ side, strike, spot, compact }: { side: "C" | "P"; strike: number; spot: number | null; compact?: boolean }) {
  const info = itmInfo(side, strike, spot);
  if (!info) {
    return <span className="text-[10px] font-mono text-slate-600">spot n/a</span>;
  }
  const { itm, distanceUsd } = info;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-bold tracking-wide ${
        itm ? "bg-rose-500/15 text-rose-300" : "bg-emerald-500/15 text-emerald-300"
      }`}
      title={itm ? `В деньгах — риск исполнения (${distanceUsd.toFixed(0)}$ за страйком)` : `Вне денег — премия тает в пользу продавца ($${distanceUsd.toFixed(0)} запас)`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${itm ? "bg-rose-400 animate-pulse" : "bg-emerald-400"}`} />
      {itm ? "ITM ⚠" : "OTM"}
      {!compact && <span className="font-mono opacity-70">·${distanceUsd.toFixed(0)}</span>}
    </span>
  );
}

function ContractChip({ c, now, onOpen }: { c: Contract; now: number; onOpen: () => void }) {
  const meta = BOT_DISPLAY[c.bot];
  const info = itmInfo(c.side, c.strike, c.spot);
  const msLeft = c.expiryMs - now;
  return (
    <button
      onClick={onOpen}
      className={`shrink-0 snap-start text-left rounded-xl border border-slate-800 bg-slate-900/80 px-3 py-2.5 ring-1 ${meta.ring}
                  hover:border-slate-600 hover:bg-slate-800/80 transition-colors min-w-[168px]`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`font-(family-name:--font-orbitron) text-[10px] font-bold tracking-widest ${meta.accent}`}>
          {meta.callsign}
        </span>
        <span
          className={`inline-block px-1 py-0.5 rounded text-[9px] font-bold ${
            c.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
          }`}
        >
          SELL {c.side}
        </span>
      </div>
      <div className="mt-1 flex items-baseline gap-1.5">
        <span className="font-mono text-sm text-slate-100">${c.strike}</span>
        {info && (
          <span className={`h-1.5 w-1.5 rounded-full ${info.itm ? "bg-rose-400 animate-pulse" : "bg-emerald-400"}`} />
        )}
      </div>
      <div className="mt-1 flex items-center justify-between">
        <Countdown expiryMs={c.expiryMs} now={now} />
        {msLeft > 0 && msLeft < 3600_000 && <span className="text-[9px] text-rose-400 uppercase tracking-wide">expiry soon</span>}
      </div>
      {c.unrealizedPnlUsd != null && (
        <div className="mt-1 text-right">
          <span className={`font-mono text-xs font-bold ${c.unrealizedPnlUsd >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
            {c.unrealizedPnlUsd >= 0 ? "+" : ""}${c.unrealizedPnlUsd.toFixed(2)}
          </span>
        </div>
      )}
    </button>
  );
}

function PairLegRow({ c, onOpen }: { c: Contract; onOpen: () => void }) {
  const info = itmInfo(c.side, c.strike, c.spot);
  return (
    <button
      onClick={onOpen}
      className="w-full flex items-center justify-between gap-2 rounded-lg px-1.5 py-1 hover:bg-slate-800/70 transition-colors"
    >
      <span className="flex items-center gap-1.5">
        <span
          className={`inline-block w-4 text-center py-0.5 rounded text-[9px] font-bold ${
            c.side === "P" ? "bg-rose-500/10 text-rose-300" : "bg-emerald-500/10 text-emerald-300"
          }`}
        >
          {c.side}
        </span>
        <span className="font-mono text-xs text-slate-200">${c.strike}</span>
        {info && (
          <span className={`h-1.5 w-1.5 rounded-full ${info.itm ? "bg-rose-400 animate-pulse" : "bg-emerald-400"}`} />
        )}
      </span>
      {c.unrealizedPnlUsd != null && (
        <span className={`font-mono text-[11px] ${c.unrealizedPnlUsd >= 0 ? "text-emerald-400/80" : "text-rose-400/80"}`}>
          {c.unrealizedPnlUsd >= 0 ? "+" : ""}{c.unrealizedPnlUsd.toFixed(2)}
        </span>
      )}
    </button>
  );
}

function PairChip({ legs, now, onOpenLeg }: { legs: Contract[]; now: number; onOpenLeg: (c: Contract) => void }) {
  const meta = BOT_DISPLAY.btc_straddle;
  const expiryMs = Math.min(...legs.map((l) => l.expiryMs));
  const msLeft = expiryMs - now;
  // Combined PnL only when BOTH legs report one — a half-known sum shown as
  // the pair total would misinform the close-all decision it exists for.
  const total = legs.reduce<number | null>(
    (s, l) => (s == null || l.unrealizedPnlUsd == null ? null : s + l.unrealizedPnlUsd),
    0,
  );
  return (
    <div
      className={`shrink-0 snap-start rounded-xl border border-slate-800 bg-slate-900/80 px-3 py-2.5 ring-1 ${meta.ring} min-w-[200px]`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className={`font-(family-name:--font-orbitron) text-[10px] font-bold tracking-widest ${meta.accent}`}>
          {meta.callsign}
        </span>
        <span className="inline-block px-1 py-0.5 rounded text-[9px] font-bold bg-orange-500/10 text-orange-300">
          SELL STRADDLE
        </span>
      </div>
      <div className="mt-1.5 flex items-baseline justify-between gap-2">
        <span className="text-[9px] uppercase tracking-wider text-slate-500">PnL пары</span>
        <span
          className={`font-mono text-base font-bold ${
            total == null ? "text-slate-500" : total >= 0 ? "text-emerald-400" : "text-rose-400"
          }`}
        >
          {total == null ? "—" : `${total < 0 ? "−" : "+"}$${Math.abs(total).toFixed(2)}`}
        </span>
      </div>
      <div className="mt-1 -mx-0.5 space-y-0.5 border-t border-slate-800/70 pt-1">
        {legs.map((l) => (
          <PairLegRow key={l.key} c={l} onOpen={() => onOpenLeg(l)} />
        ))}
      </div>
      <div className="mt-1 flex items-center justify-between">
        <Countdown expiryMs={expiryMs} now={now} />
        {msLeft > 0 && msLeft < 3600_000 && (
          <span className="text-[9px] text-rose-400 uppercase tracking-wide">expiry soon</span>
        )}
      </div>
    </div>
  );
}

// btc_straddle opens legs in C+P pairs per cycle, and the operator's decision
// unit (quick-TP $22, the $11 alert, Mission Control close-all) is the PAIR —
// so the rail merges a cycle's two legs into one block with the summed PnL
// instead of two disjoint leg chips. A lone straddle leg (sibling already
// closed by SL/TP) falls back to a normal single chip.
type RailItem =
  | { kind: "pair"; legs: Contract[]; expiryMs: number; key: string }
  | { kind: "single"; c: Contract; expiryMs: number; key: string };

function buildRailItems(contracts: Contract[]): RailItem[] {
  const pairs = new Map<string, Contract[]>();
  const singles: Contract[] = [];
  for (const c of contracts) {
    if (c.bot === "btc_straddle" && c.cycleId != null) {
      const k = `${c.bot}:${c.cycleId}`;
      const arr = pairs.get(k);
      if (arr) arr.push(c);
      else pairs.set(k, [c]);
    } else {
      singles.push(c);
    }
  }
  const items: RailItem[] = singles.map((c) => ({ kind: "single" as const, c, expiryMs: c.expiryMs, key: c.key }));
  for (const [k, legs] of pairs) {
    if (legs.length === 2) {
      legs.sort((a, b) => a.side.localeCompare(b.side)); // C above P
      items.push({ kind: "pair", legs, expiryMs: Math.min(...legs.map((l) => l.expiryMs)), key: `pair-${k}` });
    } else {
      for (const c of legs) items.push({ kind: "single", c, expiryMs: c.expiryMs, key: c.key });
    }
  }
  items.sort((a, b) => a.expiryMs - b.expiryMs);
  return items;
}

export function ActiveContractsRail({ contracts, now }: { contracts: Contract[]; now: number }) {
  const [selected, setSelected] = useState<Contract | null>(null);

  // Soonest-to-expire first — the thing the user most needs to see is the
  // contract about to roll off, not whatever happened to load first. Memoized
  // on `contracts` only — must NOT re-sort on every 1s `now` tick.
  const items = useMemo(() => buildRailItems(contracts), [contracts]);

  if (contracts.length === 0) return null;

  return (
    <>
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden glass-panel">
        <div className="px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-300 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-emerald-400 led-armed" />
            Активные контракты · {contracts.length}
          </span>
          <span className="text-[10px] text-slate-500 font-mono uppercase tracking-wide">live</span>
        </div>
        <div className="flex gap-2 overflow-x-auto px-3 py-3 snap-x">
          {items.map((it) =>
            it.kind === "pair" ? (
              <PairChip key={it.key} legs={it.legs} now={now} onOpenLeg={setSelected} />
            ) : (
              <ContractChip key={it.key} c={it.c} now={now} onOpen={() => setSelected(it.c)} />
            ),
          )}
        </div>
      </div>

      {selected && (
        <ContractDrawer contract={selected} now={now} onClose={() => setSelected(null)} />
      )}
    </>
  );
}

function ContractDrawer({ contract, now, onClose }: { contract: Contract; now: number; onClose: () => void }) {
  const meta = BOT_DISPLAY[contract.bot];
  const info = itmInfo(contract.side, contract.strike, contract.spot);
  const msLeft = contract.expiryMs - now;

  // Time-elapsed progress: only meaningful if we know when it opened.
  const totalMs = contract.openedAtMs ? contract.expiryMs - contract.openedAtMs : null;
  const elapsedPct = totalMs && totalMs > 0
    ? Math.max(0, Math.min(100, ((now - (contract.openedAtMs as number)) / totalMs) * 100))
    : null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-sm h-full bg-slate-950 border-l border-slate-800 shadow-2xl overflow-y-auto drawer-panel">
        <div className={`px-5 py-4 border-b border-slate-800 console-grid flex items-center justify-between`}>
          <div>
            <div className={`font-(family-name:--font-orbitron) text-xl font-bold tracking-wider ${meta.accent}`}>
              {meta.callsign}
            </div>
            <div className="text-[11px] text-slate-500 font-mono uppercase tracking-wide mt-0.5">
              SELL {contract.side === "P" ? "PUT" : "CALL"} · ${contract.strike} {meta.unit}
              {contract.cycleId != null && <span> · cycle #{contract.cycleId}</span>}
            </div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200 text-xl leading-none px-2">×</button>
        </div>

        <div className="p-5 space-y-5">
          {/* Big countdown */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 text-center">
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">До экспирации</p>
            <p className={`mt-1 font-mono text-3xl font-bold tabular-nums ${urgencyColor(msLeft)} ${msLeft > 0 && msLeft < 3600_000 ? "animate-pulse" : ""}`}>
              {formatCountdown(msLeft)}
            </p>
            <p className="text-[11px] text-slate-600 mt-1">
              {new Date(contract.expiryMs).toLocaleString("ru-RU")}
            </p>
            {elapsedPct != null && (
              <div className="mt-3 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                <div className="h-full bg-slate-500 rounded-full transition-all" style={{ width: `${elapsedPct}%` }} />
              </div>
            )}
          </div>

          {/* ITM/OTM */}
          <div className={`rounded-xl border p-4 ${info?.itm ? "border-rose-800/50 bg-rose-950/20" : "border-emerald-800/50 bg-emerald-950/20"}`}>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Статус позиции</p>
            {info ? (
              <>
                <p className={`mt-1 text-2xl font-bold ${info.itm ? "text-rose-300" : "text-emerald-300"}`}>
                  {info.itm ? "В деньгах (ITM)" : "Вне денег (OTM)"}
                </p>
                <p className="text-xs text-slate-400 mt-1">
                  {info.itm
                    ? `Риск исполнения — спот ${info.distanceUsd.toFixed(2)}$ (${info.distancePct.toFixed(2)}%) за страйком`
                    : `Премия тает в пользу продавца — запас $${info.distanceUsd.toFixed(2)} (${info.distancePct.toFixed(2)}%) до страйка`}
                </p>
                <p className="text-[11px] font-mono text-slate-500 mt-2">
                  Spot ${contract.spot?.toFixed(2)} · Strike ${contract.strike}
                </p>
              </>
            ) : (
              <p className="mt-1 text-sm text-slate-500">Текущая цена базового актива недоступна.</p>
            )}
          </div>

          {/* Position details */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/70 divide-y divide-slate-800">
            <Row label="Контрактов" value={`${contract.contracts.toFixed(4)} ${meta.unit}`} />
            {contract.entryCreditUsd != null && <Row label="Кредит при входе" value={`$${contract.entryCreditUsd.toFixed(2)}`} />}
            {contract.currentMarkUsd != null && <Row label="Текущая премия" value={`$${contract.currentMarkUsd.toFixed(2)}`} />}
            {contract.unrealizedPnlUsd != null && (
              <Row
                label="PnL по контракту"
                value={`${contract.unrealizedPnlUsd >= 0 ? "+" : ""}$${contract.unrealizedPnlUsd.toFixed(2)}`}
                valueClassName={contract.unrealizedPnlUsd >= 0 ? "text-emerald-400" : "text-rose-400"}
              />
            )}
            {contract.openedAtMs != null && <Row label="Открыта" value={new Date(contract.openedAtMs).toLocaleString("ru-RU")} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, valueClassName }: { label: string; value: string; valueClassName?: string }) {
  return (
    <div className="px-4 py-2.5 flex items-center justify-between text-sm">
      <span className="text-slate-500 text-xs">{label}</span>
      <span className={`font-mono text-xs ${valueClassName ?? "text-slate-200"}`}>{value}</span>
    </div>
  );
}
