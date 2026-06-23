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
    zone: "waiting" | "preparing" | "ready" | "entry";
    factors: { adx: number; mtf: number; vol: number; bull: number };
    weights: { adx: number; mtf: number; vol: number; bull: number };
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
