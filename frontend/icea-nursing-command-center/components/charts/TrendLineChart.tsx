"use client";

import React from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

type Point = { t: string; valor: number };

function makeData(): Point[] {
  const out: Point[] = [];
  const base = 100;
  for (let i = 0; i < 14; i++) {
    const v = base + Math.round((Math.sin(i / 2) * 8 + Math.random() * 6) * 10) / 10;
    out.push({ t: `D-${13 - i}`, valor: v });
  }
  return out;
}

export default function TrendLineChart() {
  const data = React.useMemo(() => makeData(), []);
  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="t" />
          <YAxis />
          <Tooltip />
          <Line type="monotone" dataKey="valor" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
