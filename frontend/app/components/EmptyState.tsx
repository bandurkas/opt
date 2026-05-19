"use client";

import type { MarketBlock, WatchItem } from "../lib/api";

export function EmptyState({
  market,
  watchlist,
}: {
  market: MarketBlock;
  watchlist: WatchItem[];
}) {
  const mtf = market.mtf;
  const direction = mtf.direction;
  const aligned = mtf.tfs_aligned;
  const regime = market.regime.regime;

  const needsForFade =
    direction === "up"
      ? "MTF разворачивается вниз (2/3 down) → активируются Put fades"
      : direction === "down"
        ? "MTF разворачивается вверх (2/3 up) → активируются Call fades"
        : "MTF становится 2/3 в одну сторону (вверх или вниз)";

  return (
    <div className="flex flex-col gap-5">
      {/* Why no signals */}
      <div className="glass-panel p-6 border border-amber-500/30 bg-amber-500/5">
        <div className="flex items-start gap-4">
          <div className="text-3xl">⏸️</div>
          <div className="flex-1">
            <h2 className="text-lg font-bold text-amber-200 mb-1">
              Сейчас нет fade-сетапа
            </h2>
            <p className="text-sm text-slate-300 leading-relaxed">
              Система ждёт когда MTF консенсус определится. Это <strong>осознанная пассивность</strong> — бэктест показал,
              что входить в момент когда тренд не сформирован = терять деньги.
            </p>

            <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
              <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/40">
                <div className="uppercase tracking-widest text-slate-500 font-bold text-[10px]">MTF сейчас</div>
                <div className="text-lg font-bold text-white mt-1">
                  {direction.toUpperCase()} <span className="text-slate-400 text-sm">({aligned}/3)</span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1">нужно ≥ 2/3</div>
              </div>
              <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/40">
                <div className="uppercase tracking-widest text-slate-500 font-bold text-[10px]">Регим</div>
                <div className="text-lg font-bold text-white mt-1">{regime}</div>
                <div className="text-[11px] text-slate-500 mt-1">ADX {market.regime.adx ?? "—"}</div>
              </div>
              <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-700/40">
                <div className="uppercase tracking-widest text-slate-500 font-bold text-[10px]">Что разблокирует</div>
                <div className="text-xs text-slate-200 mt-1 leading-snug">{needsForFade}</div>
              </div>
            </div>
            <p className="text-[11px] text-slate-500 mt-3">Обновление каждые 30 сек.</p>
          </div>
        </div>
      </div>

      {/* Watchlist */}
      {watchlist.length > 0 && (
        <div className="glass-panel p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-bold text-slate-200 uppercase tracking-widest">
              👀 Watchlist — мониторь, не торгуй
            </h3>
            <span className="text-[11px] text-slate-500">
              Топ ATM-опционов по ликвидности (не сигнал)
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {watchlist.map((w) => (
              <WatchCard key={w.symbol} item={w} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function WatchCard({ item }: { item: WatchItem }) {
  const sideColor = item.side === "Call" ? "text-emerald-400" : "text-rose-400";
  const sideBg = item.side === "Call" ? "bg-emerald-500/10" : "bg-rose-500/10";

  return (
    <div className="bg-slate-900/40 rounded-lg p-3 border border-slate-700/40 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${sideBg} ${sideColor}`}>
            {item.side.toUpperCase()}
          </span>
          <span className="text-sm font-bold text-white">${item.strike.toFixed(0)}</span>
          <span className="text-[10px] text-slate-400 font-mono">{item.expiry}</span>
        </div>
        <div className="text-[10px] text-slate-500 font-mono">
          Q {item.quality_score.toFixed(1)}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[11px] font-mono">
        <div>
          <div className="text-slate-500 text-[9px] uppercase">премия</div>
          <div className="text-slate-200">{item.quotes.mark.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-slate-500 text-[9px] uppercase">Δ delta</div>
          <div className="text-slate-200">{item.greeks.delta.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-slate-500 text-[9px] uppercase">{item.time.hours_to_expiry}ч</div>
          <div className="text-slate-200">{item.time.theta_risk}</div>
        </div>
      </div>
      <div className="flex justify-between text-[10px] text-slate-500 font-mono">
        <span>спред {item.quotes.spread_pct}%</span>
        <span>OI {item.liquidity.open_interest}</span>
      </div>
    </div>
  );
}
