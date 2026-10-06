export const EXPERIMENT_IDS = ["C03", "C10", "C21P15"] as const;
export type HourlyExperiment = typeof EXPERIMENT_IDS[number];
export type MarketTrend = {
  state: "UP" | "DOWN" | "NEUTRAL" | "UNKNOWN";
  observed_at?: number | null; latest4h_closed_at?: number | null;
  close?: number | null; sma50?: number | null; sma200?: number | null; reason?: string;
};
export const EXPERIMENTS = {
  C03: {title: "C03 · FVG 1H · по тренду BTC 4H", path: "/strategies/fvg-hourly-c03", label: "C03 · BTC 4H + докупка"},
  C10: {title: "C10 · FVG 1H · без докупки", path: "/strategies/fvg-hourly-c10", label: "C10 · без докупки"},
  C21P15: {title: "C21P15 · FVG 1H · BTC 4H + объём", path: "/strategies/fvg-hourly-c21p15", label: "C21P15 · BTC + объём + 24ч"},
} as const;
export function experimentApi(experiment: HourlyExperiment) {
  return "/api/fvg-hourly-experiments?experiment=" + experiment;
}
export function chartApi(api: string, position: string, interval: string) {
  return api + (api.includes("?") ? "&" : "?") + new URLSearchParams({kind: "chart", position, interval}).toString();
}
export function parseExperimentRequest(url: string):
  {ok: true; experiment: HourlyExperiment; upstreamPath: string} | {ok: false; status: 400; error: string} {
  const params = new URL(url).searchParams;
  const experiment = params.get("experiment");
  if (params.getAll("experiment").length !== 1 || !EXPERIMENT_IDS.includes(experiment as HourlyExperiment))
    return {ok: false, status: 400, error: "Invalid experiment"};
  const kind = params.get("kind") ?? "state";
  if (params.getAll("kind").length > 1 || (kind !== "state" && kind !== "chart"))
    return {ok: false, status: 400, error: "Invalid request kind"};
  const query = new URLSearchParams({experiment: experiment!});
  if (kind === "chart") {
    const position = params.get("position");
    const interval = params.get("interval") ?? "1H";
    if (params.getAll("position").length !== 1 || !position || position.length > 256 || /[\u0000-\u001f\u007f]/.test(position))
      return {ok: false, status: 400, error: "Invalid position"};
    if (params.getAll("interval").length > 1 || !["5m", "1H", "4H", "1D", "1W"].includes(interval))
      return {ok: false, status: 400, error: "Invalid interval"};
    query.set("position", position); query.set("interval", interval);
  }
  return {ok: true, experiment: experiment as HourlyExperiment, upstreamPath: "/" + kind + "?" + query.toString()};
}
