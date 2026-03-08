"use client";

import React from "react";
import { Button, TextInput, PasswordInput, Tile, InlineNotification } from "@carbon/react";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [err, setErr] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  const login = async () => {
    setErr(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
      });
      if (!res.ok) throw new Error(await res.text());
      router.push("/centro-mando");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-[hsl(var(--icea-surface))]">
      <Tile className="p-6 rounded-icea w-full max-w-[520px] space-y-4">
        <h1 className="text-xl font-semibold">Acceso — ICEA+ Command Center</h1>
        <p className="text-sm text-neutral-700">
          Autenticación opcional (SimpleJWT). Si el backend no requiere auth, puedes omitir.
        </p>

        {err ? <InlineNotification kind="error" lowContrast title="Error" subtitle={err} /> : null}

        <TextInput id="username" labelText="Usuario" value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
        <PasswordInput
          id="password"
          labelText="Contraseña"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />

        <Button kind="primary" onClick={login} disabled={loading || !username || !password}>
          Entrar
        </Button>
      </Tile>
    </div>
  );
}
