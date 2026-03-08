import { NextResponse } from "next/server";

const COOKIE_SECURE = (process.env.ICEA_COOKIE_SECURE || (process.env.NODE_ENV === "production" ? "true" : "false")) === "true";


export async function POST() {
  const resp = NextResponse.json({ ok: true });
  resp.headers.set("Cache-Control", "no-store");
  resp.cookies.set("icea_access", "", { httpOnly: true, sameSite: "lax", secure: COOKIE_SECURE, path: "/", maxAge: 0 });
  resp.cookies.set("icea_refresh", "", { httpOnly: true, sameSite: "lax", secure: COOKIE_SECURE, path: "/", maxAge: 0 });
  return resp;
}