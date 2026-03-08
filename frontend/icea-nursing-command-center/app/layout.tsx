import type { Metadata } from "next";
import { cookies } from "next/headers";
import "./globals.css";
import Providers from "@/components/shell/Providers";
import AppShell from "@/components/shell/AppShell";

export const metadata: Metadata = {
  title: "ICEA+ — Nursing Command Center",
  description: "Centro de control clínico-operativo de enfermería (ICEA+).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const cookieStore = cookies();
  const tenant = cookieStore.get("icea_tenant")?.value || process.env.ICEA_TENANT || "default";

  return (
    <html lang="es" data-tenant={tenant}>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
