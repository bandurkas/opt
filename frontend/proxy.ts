import { NextRequest, NextResponse } from "next/server";

// Optimistic check only (Next.js 16 Proxy, formerly Middleware): just looks
// for the mc_session cookie's presence, does NOT verify its signature/expiry
// — that's the backend's job on every /api/v1/* call (services/auth.py).
// A present-but-expired cookie still gets redirected to /login client-side
// via the 401 handler in app/lib/api.ts. Good enough as a first filter so an
// unauthenticated visitor never even sees the dashboard shell.
export default function proxy(req: NextRequest) {
  const hasSession = req.cookies.has("mc_session");
  const isLoginPage = req.nextUrl.pathname === "/login";

  if (!hasSession && !isLoginPage) {
    return NextResponse.redirect(new URL("/login", req.nextUrl));
  }
  if (hasSession && isLoginPage) {
    return NextResponse.redirect(new URL("/", req.nextUrl));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
