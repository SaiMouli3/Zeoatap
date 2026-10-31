"use client";

import type { ReactNode } from "react";
import { useApp } from "@/lib/store";
import type { Permission } from "@/lib/types";
import { EmptyState } from "./ui";

export function RequirePermission({ perm, children }: { perm: Permission; children: ReactNode }) {
  const { can } = useApp();
  if (!can(perm)) {
    return (
      <EmptyState title="This page is for administrators">
        Your role does not include access to this page. Ask a facility administrator if you need it.
      </EmptyState>
    );
  }
  return <>{children}</>;
}
