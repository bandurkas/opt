export const dynamic = "force-dynamic";
export async function GET(request: Request) {
 try {
  const auth = await fetch("http://backend:8000/api/v1/control/status", {headers:{cookie:request.headers.get("cookie")||""},cache:"no-store",signal:AbortSignal.timeout(5000)});
  if(!auth.ok) return Response.json({error:"Нужна действующая сессия дашборда"},{status:auth.status===401?401:503});
  const q=new URL(request.url).searchParams, asset=q.get("asset")||"state";
  const paths:Record<string,string>={page:"/",state:"/state",chart:"/lightweight-charts.js",candles:"/candles"};
  if(!Object.prototype.hasOwnProperty.call(paths,asset)) return new Response("Not found",{status:404});
  const frame=q.get("frame")||"5";
  if(asset==="candles" && !["1","5","15","30","60"].includes(frame)) return new Response("Invalid frame",{status:400});
  const upstream=await fetch("http://172.18.0.1:8107"+paths[asset]+(asset==="candles"?"?frame="+frame:""),{cache:"no-store",signal:AbortSignal.timeout(15000)});
  let body=await upstream.text();
  if(asset==="page") body=body.replaceAll('/lightweight-charts.js','/api/korovin?asset=chart').replaceAll("'/state'","'/api/korovin?asset=state'").replaceAll('/candles?frame=','/api/korovin?asset=candles&frame=');
  return new Response(body,{status:upstream.status,headers:{"Content-Type":upstream.headers.get("content-type")||"application/json","Cache-Control":"no-store","X-Content-Type-Options":"nosniff","X-Frame-Options":"SAMEORIGIN"}});
 } catch {return Response.json({error:"Симулятор Коровина недоступен"},{status:503});}
}
