import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import crypto from "crypto";

type Params = { path: string[] };

function backendBase(): string {
  return (process.env.ICEA_BACKEND_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
}

function withTrailingSlash(url: string): string {
  return url.endsWith("/") ? url : `${url}/`;
}

function hmacHex(secret: string, data: Uint8Array): string {
  return crypto.createHmac("sha256", secret).update(data).digest("hex");
}

function makeNonce(): string {
  // 128-bit nonce, url-safe.
  return crypto.randomBytes(16).toString("hex");
}

function canonicalEpochSeconds(): string {
  return String(Math.floor(Date.now() / 1000));
}

function concatBytes(a: Uint8Array, b: Uint8Array): Uint8Array {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

async function proxy(req: Request, params: Params): Promise<Response> {
  const path = params.path.join("/");
  const url = new URL(req.url);
  const target = new URL(withTrailingSlash(`${backendBase()}/api/v1/${path}`));
  target.search = url.search;

  // Read raw body once (prevents chunked transfer when forwarded as Buffer)
  const ab = await req.arrayBuffer();
  const body = new Uint8Array(ab);

  const headers = new Headers();
  headers.set("Accept", "application/json");
  // Preserve content type if present; otherwise set for JSON for non-empty bodies.
  const ct = req.headers.get("content-type");
  if (ct) headers.set("Content-Type", ct);
  if (!ct && body.length > 0) headers.set("Content-Type", "application/json");

  // Optional API key gate
  const apiKey = (process.env.ICEA_API_KEY || "").trim();
  if (apiKey) headers.set("X-ICEA-API-KEY", apiKey);

  // Forward bearer token from HttpOnly cookie if present
  const cookieStore = cookies();
  const access = cookieStore.get("icea_access")?.value;
  if (access) headers.set("Authorization", `Bearer ${access}`);

  // === Unified HMAC + Anti-replay (server-side only) ===
  // We try anti-replay first (ENS Alto mode). If backend is configured to require body-only signing,
  // we retry once with body-only signature to preserve backward compatibility.
  const auditSecret = (process.env.ICEA_AUDIT_SECRET || "").trim();
  const shouldAttemptHmac = auditSecret.length > 0;

  const method = req.method.toUpperCase();
  const init: RequestInit = {
    method,
    headers,
    // Always send Buffer to avoid chunked transfer encoding.
    body: method === "GET" || method === "HEAD" ? undefined : Buffer.from(body),
    redirect: "manual",
    cache: "no-store"
  };

  const attempt = async (mode: "anti" | "body"): Promise<Response> => {
    const h = new Headers(headers);

    if (shouldAttemptHmac) {
      if (mode === "anti") {
        const ts = canonicalEpochSeconds();
        const nonce = makeNonce();
        h.set("X-ICEA-Timestamp", ts);
        h.set("X-ICEA-Nonce", nonce);
        const prefix = new TextEncoder().encode(`${ts}.${nonce}.`);
        const msg = concatBytes(prefix, body);
        h.set("X-ICEA-Signature", hmacHex(auditSecret, msg));
      } else {
        h.set("X-ICEA-Signature", hmacHex(auditSecret, body));
      }
    }

    // Optional legacy federated signature (only if configured by operator).
    // WARNING: Only enable if backend has the same ICEA_FEDERATED_SECRET and legacy signing is expected.
    const fedSecret = (process.env.ICEA_FEDERATED_SECRET || "").trim();
    if (fedSecret && path.startsWith("federated/round/") && path.endsWith("/submit")) {
      // Backend legacy expects JSON canonicalization (sort_keys). Implement deterministic stringification.
      const jsonText = body.length > 0 ? new TextDecoder().decode(body) : "{}";
      let canonical = "{}";
      try {
        const parsed = JSON.parse(jsonText) as unknown;
        canonical = JSON.stringify(sortKeys(parsed));
      } catch {
        canonical = jsonText;
      }
      const sig = hmacHex(fedSecret, new TextEncoder().encode(canonical));
      h.set("X-ICEA-FED-SIG", sig);
    }

    return fetch(target.toString(), { ...init, headers: h });
  };

  let res = await attempt("anti");

  // Fallback: if backend is configured for body-only signing, anti mode will fail with 403.
  if (!res.ok && shouldAttemptHmac && (res.status === 401 || res.status === 403)) {
    // Heuristic based on backend messages
    const peek = await res.clone().text().catch(() => "");
    const looksLikeSig = /ICEA\-Signature|Invalid|Missing|Replay/i.test(peek);
    if (looksLikeSig) {
      res = await attempt("body");
    }
  }

  // Ensure no caching of sensitive payloads.
  const outHeaders = new Headers(res.headers);
  outHeaders.set("Cache-Control", "no-store");

  return new Response(res.body, { status: res.status, headers: outHeaders });
}

// Deterministic JSON sort (no `any`).
function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj).sort();
    const out: Record<string, unknown> = {};
    for (const k of keys) out[k] = sortKeys(obj[k]);
    return out;
  }
  return value;
}

export async function GET(req: Request, ctx: { params: Params }) {
  return proxy(req, ctx.params);
}
export async function POST(req: Request, ctx: { params: Params }) {
  return proxy(req, ctx.params);
}
export async function PUT(req: Request, ctx: { params: Params }) {
  return proxy(req, ctx.params);
}
export async function PATCH(req: Request, ctx: { params: Params }) {
  return proxy(req, ctx.params);
}
export async function DELETE(req: Request, ctx: { params: Params }) {
  return proxy(req, ctx.params);
}
