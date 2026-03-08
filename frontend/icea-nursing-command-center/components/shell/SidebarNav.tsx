"use client";

import React from "react";
import Link from "next/link";
import { SideNav, SideNavItems, SideNavLink } from "@carbon/react";
import { Dashboard, UserFollow, UserRole, Settings, Analyze } from "@carbon/icons-react";
import { usePathname } from "next/navigation";

type Props = {
  roles: string[];
};

function hasRole(roles: string[], required: string): boolean {
  return roles.map((r) => r.toLowerCase()).includes(required.toLowerCase());
}

export default function SidebarNav({ roles }: Props) {
  const pathname = usePathname();
  const active = (href: string) => (pathname === href ? "page" : undefined);

  return (
    <SideNav aria-label="Navegación" isFixedNav expanded>
      <SideNavItems>
        <SideNavLink as={Link} href="/centro-mando" aria-current={active("/centro-mando")} renderIcon={Dashboard}>
          Centro de mando
        </SideNavLink>

        <SideNavLink as={Link} href="/pacientes" aria-current={active("/pacientes")} renderIcon={UserFollow}>
          Pacientes (riesgo)
        </SideNavLink>

        <SideNavLink as={Link} href="/dotacion" aria-current={active("/dotacion")} renderIcon={UserRole}>
          Dotación & turnos
        </SideNavLink>

        <SideNavLink as={Link} href="/causal" aria-current={active("/causal")} renderIcon={Analyze}>
          Causal & gemelo digital
        </SideNavLink>

        {hasRole(roles, "command_center_admin") ? (
          <SideNavLink as={Link} href="/gobernanza" aria-current={active("/gobernanza")} renderIcon={Settings}>
            Gobernanza & auditoría
          </SideNavLink>
        ) : null}
      </SideNavItems>
    </SideNav>
  );
}
