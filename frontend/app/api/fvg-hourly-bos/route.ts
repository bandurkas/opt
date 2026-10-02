export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const query = new URL(request.url).searchParams;
    const kind = query.get("kind") || "state";
    if (kind !== "state" && kind !== "chart") return Response.json({error:"Unknown request"},{status:400});
    const auth = await fetch("http://backend:8000/api/v1/control/status", {
      headers:{cookie:request.headers.get("cookie")||""},cache:"no-store",signal:AbortSignal.timeout(5000),
    });
    if (!auth.ok) return Response.json({error:"Требуется действующая сессия дашборда"},{status:auth.status===401?401:503});
    const target=new URL(kind==="chart"?"/chart":"/state","http://172.18.0.1:8111");
    if (kind==="chart") {
      const position=query.get("position")||"",interval=query.get("interval")||"1H";
      if (!position||!["1H","4H","1D","1W"].includes(interval)) return Response.json({error:"Invalid chart request"},{status:400});
      target.searchParams.set("position",position);target.searchParams.set("interval",interval);
    }
    const response=await fetch(target,{cache:"no-store",signal:AbortSignal.timeout(12000)});
    return Response.json(await response.json(),{status:response.status,headers:{"Cache-Control":"no-store"}});
  } catch {
    return Response.json({error:"Часовая BOS-альтернатива недоступна"},{status:503});
  }
}
