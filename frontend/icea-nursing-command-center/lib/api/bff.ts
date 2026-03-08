import { z } from "zod";

/**
 * Client-side fetch wrapper.
 * IMPORTANT: Always call the BFF routes. Never call Django directly from the browser.
 */
export async function bffGet<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/bff${path}`, { method: "GET", cache: "no-store", ...init });
  if (!res.ok) {
    const txt = await safeText(res);
    throw new Error(`BFF GET ${path} failed: ${res.status} ${txt}`);
  }
  const json: unknown = await res.json();
  return schema.parse(json);
}

export async function bffPost<T, P>(
  path: string,
  payload: P,
  schema: z.ZodType<T>,
  init?: RequestInit
): Promise<T> {
  const res = await fetch(`/api/bff${path}`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    ...init
  });
  if (!res.ok) {
    const txt = await safeText(res);
    throw new Error(`BFF POST ${path} failed: ${res.status} ${txt}`);
  }
  const json: unknown = await res.json();
  return schema.parse(json);
}

export async function bffPostRaw(path: string, body: BodyInit, init?: RequestInit): Promise<Response> {
  const res = await fetch(`/api/bff${path}`, { method: "POST", cache: "no-store", body, ...init });
  return res;
}

async function safeText(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return "";
  }
}
