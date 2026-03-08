"use client";

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";

const MeSchema = z.object({
  authenticated: z.boolean(),
  roles: z.array(z.string()).default([]),
  subject: z.string().nullable().default(null),
});

export type Me = z.infer<typeof MeSchema>;

async function fetchMe(): Promise<Me> {
  const res = await fetch("/api/auth/me", { method: "GET", cache: "no-store" });
  if (!res.ok) {
    return { authenticated: false, roles: [], subject: null };
  }
  const json: unknown = await res.json();
  return MeSchema.parse(json);
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: fetchMe,
  });
}
