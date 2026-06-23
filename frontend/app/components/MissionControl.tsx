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
  type BotName,
  type ControlStatusResponse,
  type CredentialsInfo,
} from "../lib/api";

const REFRESH_MS = 15_000;

const BOT_LABELS: Record<BotName, string> = {
  eth_signal: "ETH Signal Bot",
  btc_straddle: "BTC Straddle",
  eth_straddle: "ETH Straddle",
};

const BOT_ORDER: BotName[] = ["eth_signal", "btc_straddle", "eth_straddle"];

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
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 px-4">
      <div className="bg-slate-900 border border-rose-800 rounded-xl p-6 max-w-md w-full space-y-4">
        <h2 className="text-lg font-semibold text-rose-400">{title}</h2>
        <p className="text-sm text-slate-300">{body}</p>
        <p className="text-xs text-slate-500">
          Введите <span className="font-mono text-slate-300">{confirmWord}</span> для подтверждения:
        </p>
        <input
          autoFocus
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono
                     focus:outline-none focus:ring-2 focus:ring-rose-500"
        />
        <div className="flex gap-2 justify-end">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 hover:bg-slate-700"
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            disabled={typed !== confirmWord || busy}
            className="px-3 py-1.5 text-sm rounded-lg bg-rose-700 hover:bg-rose-600
                       disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? "Выполняется…" : "Подтвердить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CredentialsPanel() {
  const [info, setInfo] = useState<CredentialsInfo | null>(null);
  const [open, setOpen] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchCredentials().then(setInfo).catch(() => setInfo(null));
  }, []);

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      await updateCredentials(apiKey, apiSecret);
      setApiKey("");
      setApiSecret("");
      setMsg("Ключ обновлён");
      setInfo(await fetchCredentials());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-4 py-2 bg-slate-800/50 text-xs font-semibold text-slate-400 flex justify-between items-center"
      >
        <span>Bybit API-ключ ({info?.account_name ?? "default"}) — источник: {info?.source ?? "?"}</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="p-4 space-y-3 text-sm">
          <p className="text-slate-400">
            Текущий: <span className="font-mono">{info?.api_key_masked ?? "—"}</span> / secret{" "}
            <span className="font-mono">{info?.api_secret_masked ?? "—"}</span>
          </p>
          <input
            placeholder="Новый API key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono"
          />
          <input
            placeholder="Новый API secret"
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm font-mono"
          />
          <button
            onClick={save}
            disabled={!apiKey || !apiSecret || saving}
            className="px-3 py-1.5 text-sm rounded-lg bg-sky-700 hover:bg-sky-600 disabled:opacity-40"
          >
            {saving ? "Сохранение…" : "Сохранить"}
          </button>
          {msg && <p className="text-slate-400">{msg}</p>}
          <div className="pt-2 border-t border-slate-800">
            <button
              disabled
              title="Скоро"
              className="px-3 py-1.5 text-sm rounded-lg bg-slate-800 opacity-40 cursor-not-allowed"
            >
              + Добавить другой аккаунт (скоро)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function MissionControl() {
  const [status, setStatus] = useState<ControlStatusResponse | null>(null);
  const [confirmTarget, setConfirmTarget] = useState<BotName | "global" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      setStatus(await fetchControlStatus());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, []);

  const toggle = async (bot: BotName, paused: boolean) => {
    setBusy(true);
    try {
      if (paused) await resumeBot(bot);
      else await pauseBot(bot);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const runCloseAll = async () => {
    setBusy(true);
    try {
      if (confirmTarget === "global") await closeAllBotsGlobal();
      else if (confirmTarget) await closeAllBot(confirmTarget);
      await load();
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
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-300">Управление полётами</h2>
        <div className="flex gap-2">
          <button
            onClick={() => setConfirmTarget("global")}
            className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-rose-800 hover:bg-rose-700"
          >
            🛑 Стоп + закрыть всё
          </button>
          <button
            onClick={() => logout().then(() => (window.location.href = "/login"))}
            className="px-3 py-1.5 text-xs rounded-lg bg-slate-800 hover:bg-slate-700"
          >
            Выйти
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {BOT_ORDER.map((bot) => {
          const s = status?.[bot];
          const paused = s?.paused ?? false;
          return (
            <div key={bot} className="bg-slate-900 border border-slate-800 rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{BOT_LABELS[bot]}</span>
                <span
                  className={`text-xs px-2 py-0.5 rounded-full ${
                    paused ? "bg-amber-900/50 text-amber-300" : "bg-emerald-900/50 text-emerald-300"
                  }`}
                >
                  {paused ? "Пауза" : "Работает"}
                </span>
              </div>
              <p className="text-xs text-slate-500">Открытых позиций: {s?.n_open ?? "—"}</p>
              <div className="flex gap-2">
                <button
                  onClick={() => toggle(bot, paused)}
                  disabled={busy}
                  className="flex-1 px-2 py-1.5 text-xs rounded-lg bg-slate-800 hover:bg-slate-700 disabled:opacity-40"
                >
                  {paused ? "▶ Запустить" : "⏸ Пауза"}
                </button>
                <button
                  onClick={() => setConfirmTarget(bot)}
                  disabled={busy}
                  className="flex-1 px-2 py-1.5 text-xs rounded-lg bg-rose-900/60 hover:bg-rose-800 disabled:opacity-40"
                >
                  Закрыть всё
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <CredentialsPanel />

      {confirmTarget && (
        <ConfirmModal
          title={confirmTarget === "global" ? "Остановить и закрыть ВСЁ" : `Закрыть все позиции: ${BOT_LABELS[confirmTarget]}`}
          body={
            confirmTarget === "global"
              ? "Все 3 бота будут поставлены на паузу и все открытые позиции закроются по рынку (в paper — симуляция по текущей цене; при live-торговле — реальные ордера)."
              : "Бот будет поставлен на паузу и все его открытые позиции закроются по рынку."
          }
          confirmWord={confirmTarget === "global" ? "CLOSE ALL" : confirmTarget.toUpperCase()}
          onConfirm={runCloseAll}
          onCancel={() => setConfirmTarget(null)}
          busy={busy}
        />
      )}
    </div>
  );
}
