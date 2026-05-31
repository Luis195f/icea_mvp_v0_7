"use client";

import React from "react";
import { Tag } from "@carbon/react";

type Kind = "ok" | "warning" | "danger" | "info" | "neutral";
type StatusTagType = "green" | "red" | "blue" | "cool-gray";

const map: Record<Kind, { type: StatusTagType }> = {
  ok: { type: "green" },
  warning: { type: "red" }, // Carbon doesn't have amber; red is high-contrast.
  danger: { type: "red" },
  info: { type: "blue" },
  neutral: { type: "cool-gray" }
};

export default function StatusPill({ kind, label }: { kind: Kind; label: string }) {
  return <Tag type={map[kind].type} size="sm">{label}</Tag>;
}
