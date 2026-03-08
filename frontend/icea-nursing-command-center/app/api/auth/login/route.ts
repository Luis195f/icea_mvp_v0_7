import { NextResponse } from "next/server";

const COOKIE_SECURE = (process.env.ICEA_COOKIE_SECURE || (process.env.NODE_ENV === "production" ? "true" : "false")) === "true";


type LoginBody = { username: string; password: string };

export async function POST(req: Request) {
  const body = (await req.json()) as LoginBody;

  const base = process.env.ICEA_BACKEND_BASE_URL || "http://localhost:8000";
  const url = `${base.replace(/\/$/, "")}/api/v1/auth/token/`;

  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store"
  });

  const text = await res.text();
  if (!res.ok) {
    return new NextResponse(text, { status: res.status, headers: { "Cache-Control": "no-store" } });
  }

  // Expected SimpleJWT response: { access, refresh }
  const json: unknown = JSON.parse(text);
  if (!json || typeof json !== "object") {
    return new NextResponse("Invalid token response", { status: 502, headers: { "Cache-Control": "no-store" } });
  }
  const access = (json as Record<string, unknown>)["access"];
  const refresh = (json as Record<string, unknown>)["refresh"];
  if (typeof access !== "string" || typeof refresh !== "string") {
    return new NextResponse("Missing tokens", { status: 502, headers: { "Cache-Control": "no-store" } });
  }

  const resp = NextResponse.json({ ok: true });
  resp.headers.set("Cache-Control", "no-store");

  // Store tokens in HttpOnly cookies (browser JS cannot read them).
  resp.cookies.set("icea_access", access, { httpOnly: true, sameSite: "lax", secure: COOKIE_SECURE, path: "/" });
  resp.cookies.set("icea_refresh", refresh, { httpOnly: true, sameSite: "lax", secure: COOKIE_SECURE, path: "/" });

  return resp;
}