"use client";

import React from "react";
import { Tile } from "@carbon/react";

type Props = {
  title: string;
  value: string;
  subtitle?: string;
  status?: React.ReactNode;
};

export default function MetricCard({ title, value, subtitle, status }: Props) {
  return (
    <Tile className="p-4 rounded-icea">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-neutral-700">{title}</p>
          <p className="mt-1 text-xl font-semibold truncate">{value}</p>
          {subtitle ? <p className="mt-1 text-xs text-neutral-600">{subtitle}</p> : null}
        </div>
        {status ? <div className="shrink-0">{status}</div> : null}
      </div>
    </Tile>
  );
}
