"use client";

import React from "react";
import { Header, HeaderName, SkipToContent, HeaderGlobalBar, HeaderGlobalAction } from "@carbon/react";
import { Notification, UserAvatar } from "@carbon/icons-react";
import SidebarNav from "@/components/shell/SidebarNav";
import { usePathname } from "next/navigation";
import { useMe } from "@/lib/hooks/useMe";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: me } = useMe();

  // Keep login page uncluttered.
  const isAuthPage = pathname?.startsWith("/login") ?? false;

  if (isAuthPage) {
    return <div className="min-h-screen">{children}</div>;
  }

  return (
    <div className="min-h-screen">
      <Header aria-label="ICEA+ Nursing Command Center">
        <SkipToContent />
        <HeaderName href="/centro-mando" prefix="ICEA+">
          Command Center
        </HeaderName>

        <HeaderGlobalBar>
          <HeaderGlobalAction aria-label="Notificaciones" tooltipAlignment="end">
            <Notification size={20} />
          </HeaderGlobalAction>
          <HeaderGlobalAction aria-label="Usuario" tooltipAlignment="end">
            <UserAvatar size={20} />
          </HeaderGlobalAction>
        </HeaderGlobalBar>
      </Header>

      <div className="flex">
        <SidebarNav roles={me?.roles ?? []} />
        <main id="main-content" className="w-full p-4 md:p-6">
          <div className="mx-auto max-w-[1400px]">{children}</div>
        </main>
      </div>
    </div>
  );
}
