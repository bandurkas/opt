export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") ||
  "http://localhost:8000/api/v1";

export type Side = "call" | "put" | "both";

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
  scoring: {
    score: number;
    signal: string;
    breakdown: { factor: string; points: number }[];
  };
  entry_plan: {
    limit_price: number;
    limit_price_hint: string;
    contracts: number;
    max_risk_usd: number;
    take_profit_premium: number;
    stop_loss_premium: number;
    target_spot: number;
    stop_spot: number;
    time_horizon_h: number;
  };
};

export type MarketSnapshot = {
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
};

export type TopResponse = {
  generated_at_ms: number;
  market: MarketSnapshot;
  scanned_options: number;
  top_opportunities: Opportunity[];
  disclaimer: string;
};

export async function fetchTop(params: {
  baseCoin: string;
  side: Side;
  maxDistancePct: number;
  maxHours: number;
}): Promise<TopResponse> {
  const qs = new URLSearchParams({
    base_coin: params.baseCoin,
    top_n: "3",
    max_distance_pct: String(params.maxDistancePct),
    max_hours: String(params.maxHours),
  });
  if (params.side !== "both") qs.set("side", params.side);

  const res = await fetch(`${API_BASE}/analysis/top?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${await res.text()}`);
  }
  return res.json();
}
