"use client";

import { useEffect, useState } from "react";
import {
  fetchControlStatus,
  pauseBot,
  resumeBot,
  closeAllBot,
  closeAllBotsGlobal,
  fetchCredentials,
  updateCredentials,
  logout,
  type AccountName,
  type BotName,
  type ControlStatusResponse,
  type CredentialsInfo,
} from "../lib/api";

const REFRESH_MS = 15_000;

// Identity per bot: callsign + strategy + which Bybit account (own key, own
// wallet) it authenticates as, plus an accent color for visual identity that
// stays separate from STATUS color (paused=amber, running=emerald, danger=
// rose below) so the two meanings never collide.
const BOT_META: Record<
  BotName,
  { callsign: string; strategy: string; account: AccountName; accent: string; glow: string }
> = {
  btc_straddle: {
    callsign: "BOBA1",
    strategy: "BTC · 24h short straddle",
    account: "Boba1",
    accent: "text-orange-400",
    glow: "shadow-[inset_3px_0_0_0_theme(colors.orange.500)]",
  },
  eth_straddle: {
    callsign: "GROGU1",
    strategy: "ETH · 24h short straddle",
    account: "Grogu1",
    accent: "text-cyan-400",
    glow: "shadow-[inset_3px_0_0_0_theme(colors.cyan.400)]",
  },
  eth_signal: {
    callsign: "SNIPER1",
    strategy: "ETH · signal entries (V3 hybrid)",
    account: "Sniper1",
    accent: "text-fuchsia-400",
    glow: "shadow-[inset_3px_0_0_0_theme(colors.fuchsia.400)]",
  },
};

const BOT_ORDER: BotName[] = ["btc_straddle", "eth_straddle", "eth_signal"];

function StatusLED({ paused }: { paused: boolean }) {
  return (
    <span className="relative flex items-center gap-2">
      <span
        className={`h-2.5 w-2.5 rounded-full ${
          paused ? "bg-amber-400 text-amber-400" : "bg-emerald-400 text-emerald-400 led-armed"
        }`}
      />
      <span
        className={`font-mono text-[11px] tracking-[0.2em] uppercase ${
          paused ? "text-amber-300" : "text-emerald-300"
        }`}
      >
        {paused ? "Standby" : "Armed"}
      </span>
    </span>
  );
}

function ConfirmModal({
  title,
  body,
  confirmWord,
  onConfirm,
  onCancel,
  busy,
}: {
  title: string;
  body: string;
  confirmWord: string;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [typed, setTyped] = useState("");
  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm flex items-center justify-center z-50 px-4">
      <div className="relative w-full max-w-md rounded-xl border border-rose-700/60 bg-slate-950 p-6 space-y-4 shadow-[0_0_40px_-5px_theme(colors.rose.600/0.4)]">
        <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-rose-600 via-rose-400 to-rose-600 rounded-t-xl" />
        <h2 className="font-(family-name:--font-orbitron) text-lg font-bold text-rose-400 tracking-wide">
          {title}
        </h2>
        <p className="text-sm text-slate-300">{body}</p>
        <p className="text-xs text-slate-500">
          Введите <span className="font-mono text-slate-200">{confirmWord}</span> для подтверждения:
        </p>
        <input
          autoFocus
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono tracking-wider
                     focus:outline-none focus:ring-2 focus:ring-rose-500"
        />
        <div className="flex gap-2 justify-end pt-1">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 hover:bg-slate-700"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={typed !== confirmWord || busy}
            className="px-3 py-1.5 text-sm font-semibold rounded-lg bg-rose-700 hover:bg-rose-600
                       disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? "Выполняется…" : "Подтвердить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CredentialInline({ info, onSaved }: { info: CredentialsInfo | undefined; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  if (!info) {
    return <p className="text-xs text-slate-600 font-mono">КЛЮЧ: загрузка…</p>;
  }

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await updateCredentials(info.account_name, apiKey, apiSecret);
      setApiKey("");
      setApiSecret("");
      setMsg("Ключ обновлён");
      onSaved();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border-t border-slate-800/80 pt-3">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between text-left group"
      >
        <span className="flex items-center gap-2 font-mono text-[11px] tracking-[0.15em] uppercase text-slate-500 group-hover:text-slate-300">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              info.source === "db" ? "bg-emerald-500" : "bg-slate-600"
            }`}
          />
          API-ключ {info.source === "db" ? (
            <span className="text-slate-400">
              {info.api_key_masked} / {info.api_secret_masked}
            </span>
          ) : (
            <span className="text-rose-400">не задан</span>
          )}
        </span>
        <span className="text-slate-500 text-xs">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <input
            placeholder="Новый API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono
                       focus:outline-none focus:ring-1 focus:ring-slate-600"
          />
          <input
            placeholder="Новый API secret"
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-sm font-mono
                       focus:outline-none focus:ring-1 focus:ring-slate-600"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={save}
              disabled={!apiKey || !apiSecret || saving}
              className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-40"
            >
              {saving ? "Сохранение…" : "Сохранить ключ"}
            </button>
            {msg && <p className="text-xs text-slate-400">{msg}</p>}
          </div>
        </div>
      )}
    </div>
  );
}

function BotPanel({
  bot,
  status,
  credentials,
  busy,
  onToggle,
  onCloseAll,
  onCredentialsSaved,
}: {
  bot: BotName;
  status: ControlStatusResponse[BotName] | undefined;
  credentials: CredentialsInfo | undefined;
  busy: boolean;
  onToggle: (bot: BotName, paused: boolean) => void;
  onCloseAll: (bot: BotName) => void;
  onCredentialsSaved: () => void;
}) {
  const meta = BOT_META[bot];
  const paused = status?.paused ?? false;
  const stuck = status?.close_all_requested ?? false;

  return (
    <div
      className={`relative rounded-xl border border-slate-800 bg-slate-900/70 console-grid ${meta.glow} overflow-hidden`}
    >
      <div className="p-5 flex flex-col gap-4 sm:flex-row sm:items-center">
        {/* Identity block */}
        <div className="flex items-center gap-4 sm:w-64 shrink-0">
          <div className="leading-none">
            <div
              className={`font-(family-name:--font-orbitron) text-2xl font-bold tracking-wider ${meta.accent}`}
            >
              {meta.callsign}
            </div>
            <div className="text-[11px] text-slate-500 mt-1 font-mono uppercase tracking-wide">
              {meta.strategy}
            </div>
          </div>
        </div>

        {/* Telemetry strip */}
        <div className="flex items-center gap-6 font-mono text-sm flex-1">
          <div>
            <div className="text-[10px] text-slate-500 uppercase tracking-[0.15em]">Статус</div>
            <StatusLED paused={paused} />
          </div>
          <div>
            <div className="text-[10px] text-slate-500 uppercase tracking-[0.15em]">Позиций</div>
            <div className="text-lg text-slate-100 tabular-nums">{status?.n_open ?? "—"}</div>
          </div>
          {stuck && (
            <div className="text-xs text-rose-400 font-semibold animate-pulse">
              ⚠ CLOSE-ALL В ПРОЦЕССЕ
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-2 sm:w-56 shrink-0">
          <button
            onClick={() => onToggle(bot, paused)}
            disabled={busy}
            className="flex-1 px-3 py-2 text-xs font-semibold rounded-lg bg-slate-800 hover:bg-slate-700
                       disabled:opacity-40 transition-colors"
          >
            {paused ? "▶ Запустить" : "⏸ Пауза"}
          </button>
          <button
            onClick={() => onCloseAll(bot)}
            disabled={busy}
            className="flex-1 px-3 py-2 text-xs font-semibold rounded-lg bg-rose-900/70 hover:bg-rose-800
                       disabled:opacity-40 transition-colors"
          >
            Закрыть всё
          </button>
        </div>
      </div>

      <div className="px-5 pb-4">
        <CredentialInline info={credentials} onSaved={onCredentialsSaved} />
      </div>
    </div>
  );
}

export default function MissionControl() {
  const [status, setStatus] = useState<ControlStatusResponse | null>(null);
  const [credentials, setCredentials] = useState<CredentialsInfo[]>([]);
  const [confirmTarget, setConfirmTarget] = useState<BotName | "global" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = async () => {
    try {
      setStatus(await fetchControlStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const loadCredentials = () => {
    fetchCredentials().then(setCredentials).catch(() => {});
  };

  useEffect(() => {
    loadStatus();
    loadCredentials();
    const id = setInterval(loadStatus, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const toggle = async (bot: BotName, paused: boolean) => {
    setBusy(true);
    try {
      if (paused) await resumeBot(bot);
      else await pauseBot(bot);
      await loadStatus();
    } finally {
      setBusy(false);
    }
  };

  const runCloseAll = async () => {
    setBusy(true);
    try {
      if (confirmTarget === "global") await closeAllBotsGlobal();
      else if (confirmTarget) await closeAllBot(confirmTarget);
      await loadStatus();
    } finally {
      setBusy(false);
      setConfirmTarget(null);
    }
  };

  if (error) {
    return (
      <div className="bg-rose-950/30 border border-rose-800/50 rounded-xl px-4 py-3 text-sm text-rose-300">
        Mission Control недоступен: {error}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h2 className="font-(family-name:--font-orbitron) text-sm font-bold tracking-[0.25em] uppercase text-slate-300">
            Mission Control
          </h2>
          <p className="text-[11px] text-slate-600 font-mono mt-0.5">3 бота · 3 отдельных Bybit-аккаунта</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setConfirmTarget("global")}
            className="px-4 py-2 text-xs font-bold tracking-wide rounded-lg bg-rose-700 hover:bg-rose-600
                       shadow-[0_0_20px_-4px_theme(colors.rose.500/0.6)] transition-colors"
          >
            🛑 СТОП + ЗАКРЫТЬ ВСЁ
          </button>
          <button
            onClick={() => logout().then(() => (window.location.href = "/login"))}
            className="px-3 py-2 text-xs rounded-lg bg-slate-800 hover:bg-slate-700"
          >
            Выйти
          </button>
        </div>
      </div>

      <div className="space-y-3">
        {BOT_ORDER.map((bot) => (
          <BotPanel
            key={bot}
            bot={bot}
            status={status?.[bot]}
            credentials={credentials.find((c) => c.account_name === BOT_META[bot].account)}
            busy={busy}
            onToggle={toggle}
            onCloseAll={(b) => setConfirmTarget(b)}
            onCredentialsSaved={loadCredentials}
          />
        ))}
      </div>

      {confirmTarget && (
        <ConfirmModal
          title={confirmTarget === "global" ? "Остановить и закрыть ВСЁ" : `Закрыть все позиции: ${BOT_META[confirmTarget].callsign}`}
          body={
            confirmTarget === "global"
              ? "Все 3 бота будут поставлены на паузу и все открытые позиции закроются по рынку (в paper — симуляция по текущей цене; при live-торговле — реальные ордера)."
              : "Бот будет поставлен на паузу и все его открытые позиции закроются по рынку."
          }
          confirmWord={confirmTarget === "global" ? "CLOSE ALL" : BOT_META[confirmTarget].callsign}
          onConfirm={runCloseAll}
          onCancel={() => setConfirmTarget(null)}
          busy={busy}
        />
      )}
    </div>
  );
}
