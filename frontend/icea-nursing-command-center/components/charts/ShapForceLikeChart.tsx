"use client";

import React from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ReferenceLine } from "recharts";

type Props = {
  contributions: Record<string, number[]>;
  baseValue: number;
  prediction: number;
  focusGroup: string;
};

/**
 * Clinical-friendly "force/waterfall-like" view:
 * - We get per-feature SHAP-like values by calling /icea/compute/ with group_map(feature->[feature]).
 * - We then render top +/- contributors for fast, 5-second comprehension.
 *
 * NOTE: This is not a full SHAP force plot; it is an operationally safer approximation
 * for command-center use (readable at a glance, less cognitive load).
 */
export default function ShapForceLikeChart({ contributions, baseValue, prediction, focusGroup }: Props) {
  // contributions[key] is a list per row. We render row[0].
  const rows: Array<{ nombre: string; valor: number; esEnfermeria: boolean }> = React.useMemo(() => {
    const items: Array<{ nombre: string; valor: number; esEnfermeria: boolean }> = [];
    for (const [k, arr] of Object.entries(contributions)) {
      const v = arr[0] ?? 0;
      // Skip "nursing" group if it duplicates features; we still highlight it via esEnfermeria
      if (k === focusGroup) continue;
      items.push({ nombre: k, valor: v, esEnfermeria: k.startsWith("nurse_") || k.startsWith("nic_") });
    }
    // Take top contributors by absolute value
    items.sort((a, b) => Math.abs(b.valor) - Math.abs(a.valor));
    return items.slice(0, 14);
  }, [contributions, focusGroup]);

  const nursing = contributions[focusGroup]?.[0] ?? 0;

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-icea border border-neutral-200 bg-white p-3">
          <div className="text-xs text-neutral-600">Base (expected value)</div>
          <div className="text-lg font-semibold">{baseValue.toFixed(3)}</div>
        </div>
        <div className="rounded-icea border border-neutral-200 bg-white p-3">
          <div className="text-xs text-neutral-600">ICEA (Enfermería)</div>
          <div className="text-lg font-semibold">{nursing.toFixed(3)}</div>
        </div>
        <div className="rounded-icea border border-neutral-200 bg-white p-3">
          <div className="text-xs text-neutral-600">Predicción</div>
          <div className="text-lg font-semibold">{prediction.toFixed(3)}</div>
        </div>
      </div>

      <div style={{ width: "100%", height: 320 }}>
        <ResponsiveContainer>
          <BarChart data={rows} layout="vertical" margin={{ top: 10, right: 16, bottom: 10, left: 20 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis type="number" />
            <YAxis type="category" dataKey="nombre" width={160} />
            <Tooltip />
            <ReferenceLine x={0} />
            <Bar dataKey="valor" isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="text-xs text-neutral-700">
        <span className="font-medium">Interpretación:</span> valores &gt; 0 aumentan el resultado; valores &lt; 0 lo reducen.
        ICEA resume el bloque enfermería como contribución marginal global.
      </div>
    </div>
  );
}
