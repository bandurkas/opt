export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const query = new URL(request.url).searchParams;
  const kind = query.get("kind") || "state";
  if (kind !== "state" && kind !== "chart") {
    return Response.json({ error: "Unknown request" }, { status: 400 });
  }
  const url = new URL(kind, "http://172.18.0.1:8106/");
  if (kind === "chart") {
    url.searchParams.set("symbol", query.get("symbol") || "BTCUSDT");
    url.searchParams.set("frame", query.get("frame") || "15");
    if(query.get("position")) url.searchParams.set("position", query.get("position")!);
  }
  try {
    const auth = await fetch("http://backend:8000/api/v1/control/status", {
      headers: { cookie: request.headers.get("cookie") || "" },
      cache: "no-store", signal: AbortSignal.timeout(5000),
    });
    if (!auth.ok) {
      return Response.json({ error: "Требуется действующая сессия дашборда" }, { status: auth.status === 401 ? 401 : 503 });
    }
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(12000) });
    return Response.json(await response.json(), { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return Response.json({ error: "Адаптер №6 недоступен" }, { status: 503 });
  }
}
