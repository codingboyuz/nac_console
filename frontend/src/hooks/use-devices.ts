"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "@/lib/api";
import type { Device, Page } from "@/lib/types";

/** All devices visible to the user (up to 500), for pickers and id -> hostname labels. */
export function useAllDevices() {
  return useQuery({
    queryKey: ["devices", "all"],
    queryFn: () => api.get<Page<Device>>("/devices", { limit: 500, sort: "hostname" }),
    staleTime: 60_000,
  });
}

export function useDeviceNames() {
  const { data } = useAllDevices();
  return useMemo(() => {
    const m = new Map<string, string>();
    for (const d of data?.items ?? []) m.set(d.id, d.hostname);
    return (id?: string | null) => (id ? (m.get(id) ?? id.slice(0, 8)) : "—");
  }, [data]);
}
