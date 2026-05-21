export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000/api/v1";

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

  const res = await fetch(`${API_BASE}/analysis/top?${qs.toString()}`, {
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
  n_open: number;
  n_closed: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_pnl_pct: number;
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
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
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
