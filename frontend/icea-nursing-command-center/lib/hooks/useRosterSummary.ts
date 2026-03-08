"use client";

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/api/bff";
import { RosterSummarySchema, type RosterSummary } from "@/lib/api/types";

export function useRosterSummary() {
  return useQuery<RosterSummary>({
    queryKey: ["roster-summary"],
    queryFn: () => bffGet("/roster/summary/", RosterSummarySchema),
    refetchInterval: 30_000
  });
}
