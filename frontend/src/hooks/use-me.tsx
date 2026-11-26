"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { Perm } from "@/lib/perms";
import type { Me } from "@/lib/types";

const MeContext = createContext<Me | null>(null);

export function useMeQuery() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/auth/me"), staleTime: 60_000 });
}

export function MeProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe outside MeProvider");
  return me;
}

export function useCan() {
  const me = useMe();
  return (p: Perm) => me.permissions.includes(p);
}

export function Can({ perm, children }: { perm: Perm; children: ReactNode }) {
  const can = useCan();
  return can(perm) ? <>{children}</> : null;
}
