import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { decodeJwt } from "jose";

type JwtPayload = Record<string, unknown>;

function normalizeRoles(v: unknown): string[] {
  if (typeof v === "string") {
    return v
      .split(/[,;]+/g)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (Array.isArray(v)) {
    return v.map((x) => String(x).trim()).filter(Boolean);
  }
  return [];
}

export async function GET() {
  const cookieStore = cookies();
  const access = cookieStore.get("icea_access")?.value;
  if (!access) {
    return NextResponse.json({ authenticated: false, roles: [], subject: null }, { headers: { "Cache-Control": "no-store" } });
  }

  try {
    const payload = decodeJwt(access) as JwtPayload;

    const claim = (process.env.ICEA_JWT_ROLE_CLAIM || "roles").trim() || "roles";
    const roles = [
      ...normalizeRoles(payload[claim]),
      ...normalizeRoles(payload["role"]),
      ...normalizeRoles(payload["groups"])
    ].map((r) => r.toLowerCase());

    const sub = typeof payload["sub"] === "string" ? payload["sub"] : null;

    return NextResponse.json(
      { authenticated: true, roles: Array.from(new Set(roles)), subject: sub },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch {
    return NextResponse.json({ authenticated: false, roles: [], subject: null }, { headers: { "Cache-Control": "no-store" } });
  }
}
