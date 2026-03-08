"use client";

import { useQuery } from "@tanstack/react-query";
import { bffGet } from "@/lib/api/bff";
import { DashboardSummarySchema, type DashboardSummary } from "@/lib/api/types";

export function useDashboardSummary() {
  return useQuery<DashboardSummary>({
    queryKey: ["dashboard-summary"],
    queryFn: () => bffGet("/dashboard/summary/", DashboardSummarySchema),
    refetchInterval: 10_000
  });
}
