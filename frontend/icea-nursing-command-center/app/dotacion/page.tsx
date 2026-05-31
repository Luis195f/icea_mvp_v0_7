"use client";

import React from "react";
import { Tile, Button, InlineNotification, FileUploader } from "@carbon/react";
import { useRosterSummary } from "@/lib/hooks/useRosterSummary";

export default function DotacionPage() {
  const { data, isLoading, error, refetch } = useRosterSummary();
  const [unitId, setUnitId] = React.useState<string>("1");
  const [csv, setCsv] = React.useState<string>("");

  const upload = async () => {
    try {
      const res = await fetch("/api/bff/roster/upload-csv/", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unit_id: Number(unitId), csv })
      });
      if (!res.ok) throw new Error(await res.text());
      setCsv("");
      await refetch();
      alert("Roster cargado.");
    } catch (e) {
      alert(`Error cargando roster: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Dotación & turnos</h1>
        <p className="text-sm text-neutral-700">
          Subida de roster (CSV) para monitorización agregada ajustada por agudeza; sin ranking por turno pequeño o profesional.
        </p>
      </div>

      <Tile className="p-4 rounded-icea">
        {isLoading ? <p className="text-sm text-neutral-700">Cargando…</p> : null}
        {error ? <InlineNotification kind="error" lowContrast title="Error" subtitle={error.message} /> : null}
        {data ? (
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <div className="text-xs text-neutral-600">Turnos roster</div>
              <div className="text-xl font-semibold">{data.roster_shifts}</div>
            </div>
            <div>
              <div className="text-xs text-neutral-600">Unidades con roster</div>
              <div className="text-xl font-semibold">{data.units_with_roster}</div>
            </div>
            <div className="flex items-end justify-end">
              <Button kind="secondary" size="sm" onClick={() => refetch()}>
                Refrescar
              </Button>
            </div>
          </div>
        ) : null}
      </Tile>

      <Tile className="p-4 rounded-icea space-y-4">
        <h2 className="text-lg font-semibold">Cargar CSV</h2>
        <p className="text-sm text-neutral-700">
          Formato esperado por backend: columnas (ej.): <code>start_dt,end_dt,rn_count,na_count,patient_census</code>.
        </p>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm font-medium">unit_id</label>
            <input
              className="w-full rounded-icea border border-neutral-300 px-3 py-2 text-sm"
              value={unitId}
              onChange={(e) => setUnitId(e.currentTarget.value)}
              placeholder="1"
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">CSV (pegar texto)</label>
            <textarea
              className="w-full rounded-icea border border-neutral-300 px-3 py-2 text-sm min-h-[140px]"
              value={csv}
              onChange={(e) => setCsv(e.currentTarget.value)}
              placeholder="start_dt,end_dt,rn_count,na_count,patient_census\n2026-03-01T07:00:00Z,2026-03-01T19:00:00Z,6,2,24"
            />
          </div>
        </div>

        <div className="flex gap-2">
          <Button kind="primary" onClick={upload} disabled={!csv.trim()}>
            Subir roster
          </Button>
        </div>

        <div className="text-xs text-neutral-600">
          Nota: también puedes integrarlo vía ETL/HRIS; aquí se ofrece un canal mínimo para piloto.
        </div>
      </Tile>
    </div>
  );
}
