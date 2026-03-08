"use client";

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/api/bff";
import { AuditEventsSchema, type AuditEvents } from "@/lib/api/types";

export function useAuditEvents(limit: number) {
  const q = new URLSearchParams({ limit: String(limit) }).toString();
  return useQuery<AuditEvents>({
    queryKey: ["audit-events", limit],
    queryFn: () => bffGet(`/governance/audit/events/?${q}`, AuditEventsSchema),
    refetchInterval: 15_000
  });
}
