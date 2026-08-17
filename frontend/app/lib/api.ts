export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000/api/v1";

// Every request carries the mc_session cookie (Mission Control auth). A 401
// means the session is missing/expired — bounce to /login from one place
// instead of every call site having to handle it.
async function authedFetch(url: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(url, { ...init, credentials: "include" });
  if (res.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
    window.location.href = "/login";
  }
  return res;
}

export type Side = "call" | "put" | "both";

export type TFAnalysis = {
  direction: "up" | "down" | "neutral" | "unknown";
  strength: number;
  momentum: "accelerating" | "decelerating" | "divergent" | "flat" | "unknown";
  ema20: number | null;
  ema50: number | null;
  rsi: number | null;
  volume_zscore: number | null;
  change_pct: number;
  last_close: number;
};

export type MTF = {
  direction: "up" | "down" | "neutral";
  agreement: number;
  tfs_aligned: number;
  tfs_total: number;
  accelerating: boolean;
  tf_5m: TFAnalysis;
  tf_15m: TFAnalysis;
  tf_1h: TFAnalysis;
};

export type Regime = {
  regime: "trend" | "range" | "transition" | "unknown";
  adx: number | null;
  trend_strength: number;
};

export type IVMetrics = {
  current_iv: number | null;
  iv_change_1h_pct: number | null;
  iv_change_24h_pct: number | null;
  iv_rank_7d: number | null;
  history_points_7d: number;
  trend_1h: "rising" | "falling" | "stable" | "unknown";
};

export type ExitLeg = {
  premium: number;
  spot: number;
  contracts_to_close: number;
  profit_usd?: number;
  loss_usd?: number;
};

export type ExitPlan = {
  valid: boolean;
  regime_used?: string;
  tp1?: ExitLeg;
  tp2?: ExitLeg;
  sl?: ExitLeg;
  trail_rule?: string;
  trail_atr_15m?: number | null;
  time_stop_hours?: number;
  summary?: {
    best_case_profit_usd: number;
    worst_case_loss_usd: number;
    risk_reward: number | null;
  };
};

export type SignalType = "continuation" | "pullback" | "fade";
export type Strategy = "fade_long_dated" | "trend_continuation_legacy";

export type Scoring = {
  signal_type: SignalType;
  score: number;
  signal: string;
  recommendation: string;
  breakdown: { factor: string; points: number }[];
  theta_decay_probability: number;
  theta_decay_class: "low" | "medium" | "high" | "critical";
  setup_reason?: string;
};

export type Opportunity = {
  symbol: string;
  side: "Call" | "Put";
  strike: number;
  expiry: string;
  expiry_iso: string;
  underlying_price: number;
  spot: number;
  distance: { distance_usd: number; distance_percent: number };
  time: {
    hours_to_expiry: number;
    minutes_to_expiry: number;
    theta_risk: string;
    expiry_iso: string;
  };
  quotes: { bid: number; ask: number; mark: number; spread_pct: number };
  greeks: {
    delta: number;
    gamma: number;
    vega: number;
    theta: number;
    iv: number;
  };
  liquidity: { open_interest: number; volume_24h: number };
  iv_metrics: IVMetrics;
  scoring: Scoring;
  entry_plan: {
    action: string;
    position_summary: string;
    symbol_to_search: string;
    limit_price: number;
    contracts: number;
    total_cost_usd: number;
    max_risk_usd: number;
    max_risk_note: string;
    exits: ExitPlan;
    bybit_steps: string[];
    limit_price_hint: string;
  };
};

export type MarketBlock = {
  spot: number;
  direction: "bullish" | "bearish" | "neutral";
  momentum_strong: boolean;
  volume_spike: boolean;
  rsi_1h: number;
  ema_fast: number;
  ema_slow: number;
  change_1h_pct: number;
  change_4h_pct: number;
  nearest_resistance: number;
  nearest_support: number;
  fetched_at_ms: number;
  mtf: MTF;
  regime: Regime;
  atr_15m: number | null;
};

export type WatchItem = {
  symbol: string;
  side: "Call" | "Put";
  strike: number;
  expiry: string;
  spot: number;
  distance: { distance_usd: number; distance_percent: number };
  time: { hours_to_expiry: number; theta_risk: string; expiry_iso: string };
  quotes: { bid: number; ask: number; mark: number; spread_pct: number };
  greeks: { delta: number; iv: number; theta: number };
  liquidity: { open_interest: number; volume_24h: number };
  quality_score: number;
};

export type TopResponse = {
  generated_at_ms: number;
  market: MarketBlock;
  data_freshness: {
    candles_5m: number;
    candles_15m: number;
    candles_1h: number;
    last_snapshot_age_s: number | null;
  };
  scanned_options: number;
  top_opportunities: Opportunity[];
  watchlist?: WatchItem[];
  disclaimer: string;
};

export async function fetchTop(params: {
  baseCoin: string;
  side: Side;
  maxDistancePct: number;
  maxHours?: number;
  minHours?: number;
  minScore?: number;
  riskBudgetUsd?: number;
  strategy?: Strategy;
  includePullback?: boolean;
  includeContinuation?: boolean;
}): Promise<TopResponse> {
  const qs = new URLSearchParams({
    base_coin: params.baseCoin,
    top_n: "3",
    max_distance_pct: String(params.maxDistancePct),
  });
  if (params.side !== "both") qs.set("side", params.side);
  if (params.maxHours !== undefined) qs.set("max_hours", String(params.maxHours));
  if (params.minHours !== undefined) qs.set("min_hours", String(params.minHours));
  if (params.minScore !== undefined) qs.set("min_score", String(params.minScore));
  if (params.riskBudgetUsd !== undefined) qs.set("risk_budget_usd", String(params.riskBudgetUsd));
  if (params.strategy) qs.set("strategy", params.strategy);
  if (params.includePullback !== undefined) qs.set("include_pullback", String(params.includePullback));
  if (params.includeContinuation !== undefined) qs.set("include_continuation", String(params.includeContinuation));

  const res = await authedFetch(`${API_BASE}/analysis/top?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// ───────────────────────── Paper trading ─────────────────────────

export type PaperState = {
  start_equity_usd: number;
  started_at_ms: number;
  cb_cooldown_until_ms: number;
  cb_active: boolean;
  consec_losses: number;
  current_equity_usd: number;
  realized_usd: number;
  unrealized_usd?: number;
  max_dd_pct?: number;
  n_open: number;
  n_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_pnl_pct: number;
  exit_counts?: Record<string, number>;
  last_signal_ts_ms: number | null;
  last_signal_age_h: number | null;
  bars_since_last_signal_5m: number | null;
  signals_24h: number;
  window_5m_bars: number;
};

export type PaperPosition = {
  id: number;
  opened_at_ms: number;
  underlying_at_open: number;
  side: "C" | "P";
  strike: number;
  expiry_ms: number;
  contracts: number;
  size_usd: number;
  entry_credit_usd: number;
  entry_credit_pct: number;
  entry_source: string;
  status: string;
  tp1_pct: number;
  tp2_pct: number;
  sl_pct: number;
  hold_h: number;
  half_closed_at_ms: number | null;
  closed_at_ms: number | null;
  exit_debit_usd: number | null;
  pnl_pct: number | null;
  pnl_usd: number | null;
  exit_reason: string | null;
  current_mark_usd?: number | null;
  unrealized_pnl_usd?: number | null;
};

export type EquityPoint = {
  ts_ms: number;
  equity: number;
  realized: number;
  unrealized: number;
  n_open: number;
  n_closed: number;
};

async function jget<T>(path: string): Promise<T> {
  const res = await authedFetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function jpost<T>(path: string, body?: unknown): Promise<T> {
  const res = await authedFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function fetchPaperState(): Promise<PaperState> {
  return jget<PaperState>(`/paper/state`);
}

export async function fetchPaperPositions(
  status: "open" | "recent" = "open",
  limit = 50,
): Promise<{ positions: PaperPosition[]; count: number }> {
  return jget(`/paper/positions?status=${status}&limit=${limit}`);
}

export async function fetchEquityHistory(
  hours = 168,
): Promise<{ hours: number; points: EquityPoint[] }> {
  return jget(`/paper/equity_history?hours=${hours}`);
}

export type PaperConditions = {
  ready: boolean;
  active_side: "P" | "C" | null;
  dead_zone: boolean;
  ret_7d: number | null;
  vol_high: boolean;
  regime_ok: boolean;
  mtf_direction_ok: boolean;
  bull_filter_ok: boolean;
  spot: number | null;
  vol_pctile: number | null;
  regime: string | null;
  mtf_direction: string | null;
  mtf_aligned_count: number | null;
  ema_ratio: number | null;
  checked_at_ms: number;
  bars_available: { "5m": number; "15m": number; "1h": number };
  adx?: {
    score: number;
    adx: number | null;
    plus_di: number | null;
    minus_di: number | null;
    adx_slope_6h: number;
    di_spread: number;
    components: { base: number; slope_bonus: number; di_bonus: number };
  };
  proximity?: {
    proximity_pct: number;
    zone: "waiting" | "preparing" | "ready" | "entry" | "side-off";
    factors: { adx: number; mtf: number; vol: number; regime: number; bull: number };
    weights: { adx: number; mtf: number; vol: number; regime: number; bull: number };
    debounce_unknown: boolean;
    window_disqualified: boolean;
  };
  thresholds?: {
    ret_threshold_put: number;
    ret_threshold_call: number;
    ret_7d: number | null;
    active_side: "P" | "C" | null;
    dead_zone: boolean;
    vol_threshold?: number;
    regime_filter?: string[];
    mtf_direction_filter?: string | null;
    mtf_min_aligned?: number;
    bull_market_ratio_max?: number | null;
  };
};

export async function fetchPaperConditions(): Promise<PaperConditions> {
  return jget(`/paper/conditions`);
}

export async function fetchRecentTrades(limit = 100): Promise<{ positions: PaperPosition[]; count: number }> {
  return jget(`/paper/positions?status=recent&limit=${limit}`);
}

// ───────────────────────── BTC straddle bot ─────────────────────────
// Separate book from the ETH paper trader above — own tables/endpoints, same shape.

export type BtcStraddleState = {
  start_equity_usd: number;
  started_at_ms: number;
  last_cycle_id: number;
  current_equity_usd: number;
  realized_usd: number;
  unrealized_usd: number;
  max_dd_pct: number;
  n_open: number;
  n_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_pnl_pct: number;
  exit_counts?: Record<string, number>;
};

export type BtcStraddlePosition = {
  id: number;
  cycle_id: number;
  leg: "C" | "P";
  opened_at_ms: number;
  underlying_at_open: number;
  strike: number;
  expiry_ms: number;
  contracts: number;
  size_usd: number;
  entry_credit_usd: number;
  entry_credit_pct: number;
  entry_source: string;
  status: string;
  margin_per_lot_usd: number;
  sl_dollar_trip_usd: number;
  closed_at_ms: number | null;
  exit_debit_usd: number | null;
  pnl_pct: number | null;
  pnl_usd: number | null;
  exit_reason: string | null;
  current_mark_usd?: number | null;
  unrealized_pnl_usd?: number | null;
};

export async function fetchBtcStraddleState(): Promise<BtcStraddleState> {
  return jget(`/btc-straddle/state`);
}

export async function fetchBtcStraddlePositions(
  status: "open" | "recent" = "open",
  limit = 50,
): Promise<{ positions: BtcStraddlePosition[]; count: number }> {
  return jget(`/btc-straddle/positions?status=${status}&limit=${limit}`);
}

export async function fetchBtcStraddleEquityHistory(
  hours = 168,
): Promise<{ hours: number; points: EquityPoint[] }> {
  return jget(`/btc-straddle/equity_history?hours=${hours}`);
}

// ───────────────────────── ETH straddle bot ─────────────────────────
// Separate book from both the ETH signal trader and the BTC straddle above —
// own tables/endpoints, same shape as BtcStraddle*.

export type EthStraddleState = BtcStraddleState;
export type EthStraddlePosition = BtcStraddlePosition;

export async function fetchEthStraddleState(): Promise<EthStraddleState> {
  return jget(`/eth-straddle/state`);
}

export async function fetchEthStraddlePositions(
  status: "open" | "recent" = "open",
  limit = 50,
): Promise<{ positions: EthStraddlePosition[]; count: number }> {
  return jget(`/eth-straddle/positions?status=${status}&limit=${limit}`);
}

export async function fetchEthStraddleEquityHistory(
  hours = 168,
): Promise<{ hours: number; points: EquityPoint[] }> {
  return jget(`/eth-straddle/equity_history?hours=${hours}`);
}

export type Kline = {
  start_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

// Generic shape — the same straddle-chart UI will be reused for Boba1 (BTC)
// and Sniper1 once their bots expose an analogous /chart endpoint.
export type StraddleChartLeg = {
  id: number;
  leg: "C" | "P";
  strike: number;
  expiry_ms: number;
  entry_credit_usd: number;
  current_mark_usd: number | null;
  sl_dollar_trip_usd: number;
  sl_progress_pct: number | null;
  risk_per_contract_usd: number;
  reward_per_contract_usd: number;
  sl_price_approx: number | null;
  tp_price_approx: number | null;
};
export type EthStraddleChartLeg = StraddleChartLeg;

export async function fetchEthStraddleChart(
  klineLimit = 288,
): Promise<{ spot: number | null; klines: Kline[]; legs: StraddleChartLeg[] }> {
  return jget(`/eth-straddle/chart?kline_limit=${klineLimit}`);
}

// BTC straddle has no /chart endpoint (no candlestick view built for it yet) —
// just the live spot, which is all ITM/OTM status needs.
export async function fetchBtcPrice(): Promise<{ symbol: string; price: number }> {
  return jget(`/market/btc-price`);
}

// ───────────────────────── Mission Control: auth ─────────────────────────

export async function login(password: string): Promise<void> {
  const res = await authedFetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) throw new Error(res.status === 401 ? "Неверный пароль" : `API ${res.status}`);
}

export async function logout(): Promise<void> {
  await jpost(`/auth/logout`);
}

// ───────────────────────── Mission Control: bot control ─────────────────────────

export type BotName = "eth_signal" | "btc_straddle" | "eth_straddle";

export type BotControlStatus = {
  paused: boolean;
  close_all_requested: boolean;
  n_open: number;
};

export type ControlStatusResponse = Record<BotName, BotControlStatus>;

export async function fetchControlStatus(): Promise<ControlStatusResponse> {
  return jget(`/control/status`);
}

export async function pauseBot(bot: BotName): Promise<void> {
  await jpost(`/control/${bot}/pause`);
}

export async function resumeBot(bot: BotName): Promise<void> {
  await jpost(`/control/${bot}/resume`);
}

export async function closeAllBot(bot: BotName): Promise<void> {
  await jpost(`/control/${bot}/close-all`);
}

export async function closeAllBotsGlobal(): Promise<void> {
  await jpost(`/control/close-all`);
}

// ───────────────────────── Mission Control: settings ─────────────────────────

// Bybit account call signs — deliberately separate from BotName (the
// control_repo pause/close-all key): Boba1=BTC straddle, Grogu1=ETH straddle,
// Sniper1=ETH signal bot.
export type AccountName = "Boba1" | "Grogu1" | "Sniper1";

export type CredentialsInfo = {
  account_id: number;
  account_name: AccountName;
  label: string;
  api_key_masked: string | null;
  api_secret_masked: string | null;
  source: "db" | "none";
};

// One Bybit account per bot (own key, own wallet).
export async function fetchCredentials(): Promise<CredentialsInfo[]> {
  return jget(`/settings/credentials`);
}

export async function updateCredentials(accountName: AccountName, apiKey: string, apiSecret: string): Promise<void> {
  await jpost(`/settings/credentials/${accountName}`, { api_key: apiKey, api_secret: apiSecret });
}

// ───────────────────────── Jony (separate service, own API) ─────────────────────────
// Multi-asset VRP basket paper bot (ETH P+C, BTC Call-only), /root/Jony on
// VPS3, SQLite behind :8200 — same "fully separate service" pattern as
// archived Tyagach used to.

export const JONY_API_BASE =
  process.env.NEXT_PUBLIC_JONY_API_URL?.replace(/\/+$/, "") ||
  "http://187.127.114.34:8200";

export type JonyState = {
  initialized: boolean;
  started_at_ms: number;
  start_equity_usd: number;
  equity_usd: number;
  cb_cooldown_until_ms: number;
  paused: boolean;
  n_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  total_pnl_usd: number;
  open_position_count: number;
  max_dd_pct: number;
};

export type JonyPosition = {
  id: number;
  coin: "ETH" | "BTC";
  side: "C" | "P";
  option_symbol: string;
  strike: number;
  expiry_ms: number;
  qty: number;
  opened_at_ms: number;
  underlying_at_open: number;
  entry_credit: number;
  entry_source: string;
  margin_usd: number;
  tp2_pct: number;
  sl_pct: number;
  hold_h: number;
  status: string;
  closed_at_ms: number | null;
  exit_debit: number | null;
  exit_reason: string | null;
  pnl_pct: number | null;
  pnl_usd: number | null;
  current_mark_usd?: number | null;
  unrealized_pnl_usd?: number | null;
};

export type JonyParams = {
  coins: Record<string, string[]>;
  put_gen: { vol_threshold: number; regime_filter: string[]; mtf_direction_filter: string };
  put_gen_by_coin?: Record<string, { vol_threshold: number; regime_filter: string[]; mtf_direction_filter: string }>;
  ret_7d_threshold?: number;
  call_gen: { vol_threshold: number; regime_filter: string[]; mtf_direction_filter: string; bull_market_ratio_max: number };
  put_exit: { tp2_pct: number; sl_pct: number; hold_h: number };
  call_exit: { tp2_pct: number; sl_pct: number; hold_h: number };
  account: {
    start_equity_usd: number; margin_pct_per_trade: number;
    max_open_positions: number; per_coin_cap: number; port_margin_cap: number;
    cb_consec_limit: number; cb_pause_hours: number; cooldown_min: number;
    target_expiry_h: number;
  };
  backtest: { finding: string; full_return_pct: number; max_dd_pct: number; holdout_return_pct: number; trades_per_day: number };
};

async function jonyGet<T>(path: string): Promise<T> {
  const res = await fetch(`${JONY_API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Jony API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function fetchJonyState(): Promise<JonyState> {
  return jonyGet(`/state`);
}

export async function fetchJonyParams(): Promise<JonyParams> {
  return jonyGet(`/params`);
}

export async function fetchJonyPositions(limit = 200): Promise<{ open: JonyPosition[]; recent: JonyPosition[] }> {
  return jonyGet(`/positions?limit=${limit}`);
}

export type JonyProximity = {
  proximity_pct: number;
  zone: "waiting" | "preparing" | "ready" | "entry" | "side-off";
  factors: { vol: number; regime: number; mtf: number; bull: number };
  weights: { vol: number; regime: number; mtf: number; bull: number };
  debounce_unknown: boolean;
  window_disqualified: boolean;
  active_side: "P" | "C" | null;
};

// Display-only (core/proximity.py) — never drives sizing/entries. Reads
// whatever the loop's last per-minute gate check persisted; 100% is only
// ever shown once the live debounce window is confirmed non-disqualified
// AT the close-tick minute, not from the raw gate snapshot alone.
export async function fetchJonyProximity(): Promise<Record<"ETH" | "BTC", JonyProximity>> {
  return jonyGet(`/proximity`);
}

// Jony's /equity rows are {ts_ms, equity_usd (realized), unrealized_usd,
// open_positions} — mapped into EquityPoint (equity = realized + unrealized
// mark-to-market) so the shared EquityChart renders without branching.
export async function fetchJonyEquityHistory(limit = 2000): Promise<EquityPoint[]> {
  const rows = await jonyGet<{ ts_ms: number; equity_usd: number; unrealized_usd: number; open_positions: number }[]>(`/equity?limit=${limit}`);
  return rows.map((r) => ({
    ts_ms: r.ts_ms,
    equity: r.equity_usd + r.unrealized_usd,
    realized: r.equity_usd,
    unrealized: r.unrealized_usd,
    n_open: r.open_positions,
    n_closed: 0,
  }));
}

async function jonyPost<T>(path: string): Promise<T> {
  const res = await fetch(`${JONY_API_BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`Jony API ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function pauseJony(): Promise<void> {
  await jonyPost(`/pause`);
}

export async function resumeJony(): Promise<void> {
  await jonyPost(`/resume`);
}

// Sets a flag; Jony's LOOP executes the buybacks on its next ~5s tick
// (single-writer discipline), so positions may take a few seconds to vanish.
export async function closeAllJony(): Promise<void> {
  await jonyPost(`/close_all`);
}

// Same single-writer/~5s-tick pattern as closeAllJony, scoped to one
// position — does NOT pause the bot. 404s if the position already closed
// (e.g. hit SL/TP/expiry) between the dashboard rendering it and the click.
export async function closeJonyPosition(id: number): Promise<void> {
  await jonyPost(`/close_position/${id}`);
}

export type JonyChartData = {
  coins: Record<string, { klines: Kline[]; spot: number | null }>;
  positions: JonyPosition[];
};

export async function fetchJonyChart(klineLimit = 288): Promise<JonyChartData> {
  return jonyGet(`/chart?kline_limit=${klineLimit}`);
}

// ───────────────────────── BUBU (separate service, own API) ─────────────────────────
// Grid DCA + range-scalp paper bot, BTCUSDT perp on Bybit, /root/BUBU on
// VPS3, SQLite behind :8300 — same "fully separate service" pattern as
// Jony above. v1 baseline strategy only, $300 starting balance.
// At most ONE open cycle at a time (unlike Jony's multi-position book).

export const BUBU_API_BASE =
  process.env.NEXT_PUBLIC_BUBU_API_URL?.replace(/\/+$/, "") ||
  "http://187.127.114.34:8300/api/v1/bubu";

async function bget<T>(path: string): Promise<T> {
  const res = await fetch(`${BUBU_API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`BUBU API ${res.status}: ${await res.text()}`);
  return res.json();
}

async function bpost<T>(path: string): Promise<T> {
  const res = await fetch(`${BUBU_API_BASE}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`BUBU API ${res.status}: ${await res.text()}`);
  return res.json();
}

export type BubuFill = {
  kind: "grid" | "range_buy" | "range_sell";
  ts: number;
  price: number;
  qty: number;
  level?: number;
};

export type BubuOpenCycle = {
  start_ts: number;
  levels_reached: number;
  live_qty: number;
  live_avg_price: number;
  leverage_used: number;
  liq_price: number | null;
  grid_pnl_mtm: number;
  range_pnl: number;
  funding_paid: number;
  fees_paid: number;
  range_trades: number;
  range_top: number | null;
  range_bottom: number | null;
  range_status: "idle" | "bought_waiting_sell" | null;
  fills: BubuFill[];
};

export type BubuState = {
  symbol: string;
  balance_usdt: number;
  equity_usd: number;
  unrealized_usd: number;
  start_balance_usdt: number;
  started_at_ms: number | null;
  paused: boolean;
  leverage: number;
  open_cycle: BubuOpenCycle | null;
  n_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  realized_usd: number;
  max_dd_pct: number;
};

export type BubuCycle = {
  id: number;
  start_ts: number;
  end_ts: number | null;
  end_reason: "tp" | "bust" | "emergency_close" | "manual_close" | "open_at_end" | null;
  status: "open" | "closed";
  levels_reached: number;
  grid_pnl: number;
  range_pnl: number;
  funding_paid: number;
  fees_paid: number;
  margin_used: number;
  range_trades: number;
  leverage_used: number;
  fills: BubuFill[];
};

export async function fetchBubuState(): Promise<BubuState> {
  return bget(`/state`);
}

export async function fetchBubuCycles(
  status: "open" | "closed" | null = null,
  limit = 200,
): Promise<BubuCycle[]> {
  const qs = status ? `status=${status}&limit=${limit}` : `limit=${limit}`;
  return bget(`/cycles?${qs}`);
}

// BUBU's equity_history returns {ts_ms, balance_usdt} rows (same shape as
// Jony's) — mapped into EquityPoint so the same EquityChart component
// renders it without bot-specific branching.
export async function fetchBubuEquityHistory(limit = 2000): Promise<EquityPoint[]> {
  const rows = await bget<{ ts_ms: number; balance_usdt: number }[]>(`/equity_history?limit=${limit}`);
  return rows.map((r) => ({ ts_ms: r.ts_ms, equity: r.balance_usdt, realized: 0, unrealized: 0, n_open: 0, n_closed: 0 }));
}

export type BubuChartOverlay = {
  avg_price: number;
  tp_price: number;
  liq_price: number | null;
  levels_reached: number;
  range_top: number | null;
  range_bottom: number | null;
  fills: BubuFill[];
};

export async function fetchBubuChart(
  klineLimit = 500,
): Promise<{ spot: number | null; klines: Kline[]; overlay: BubuChartOverlay | null }> {
  return bget(`/chart?kline_limit=${klineLimit}`);
}

export async function pauseBubu(): Promise<void> {
  await bpost(`/pause`);
}

export async function resumeBubu(): Promise<void> {
  await bpost(`/resume`);
}

// Single open-cycle close (BUBU never holds more than one) — does NOT pause
// the bot, same convention as Jony's per-position close_position.
export async function closeBubuPosition(): Promise<void> {
  await bpost(`/close_position`);
}
