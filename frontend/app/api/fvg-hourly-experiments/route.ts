import {parseExperimentRequest} from "../../lib/fvgHourlyExperiments";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const parsed = parseExperimentRequest(request.url);
    if (!parsed.ok) return Response.json({error:parsed.error},{status:parsed.status});
    const auth = await fetch("http://backend:8000/api/v1/control/status", {
      headers: {cookie:request.headers.get("cookie")||""},cache:"no-store",signal:AbortSignal.timeout(5000),
    });
    if (!auth.ok) return Response.json({error:"Требуется действующая сессия дашборда"},{status:auth.status===401?401:503});
    // Fixed private bridge; only validated fields are copied, never a caller-supplied URL/host.
    const target = new URL(parsed.upstreamPath,"http://172.18.0.1:8114");
    const response = await fetch(target,{cache:"no-store",signal:AbortSignal.timeout(12000)});
    return Response.json(await response.json(),{status:response.status,headers:{"Cache-Control":"no-store"}});
  } catch {
    return Response.json({error:"Часовые FVG эксперименты недоступны"},{status:503});
  }
}
