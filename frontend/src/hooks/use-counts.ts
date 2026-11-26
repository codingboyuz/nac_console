"use client";

import { useQueries } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Page } from "@/lib/types";

/**
 * Totals of a paged list endpoint for several values of one filter (limit=1 requests).
 * Returns { "": total, [value]: count } - undefined while loading.
 */
export function useCounts(path: string, param: string, values: string[], options: { enabled?: boolean; refetchInterval?: number; base?: Record<string, string> } = {}) {
  const keys = ["", ...values];
  const results = useQueries({
    queries: keys.map((v) => ({
      queryKey: ["counts", path, options.base ?? {}, param, v],
      queryFn: () => api.get<Page<unknown>>(path, { ...options.base, ...(v ? { [param]: v } : {}), limit: 1 }).then((p) => p.total),
      enabled: options.enabled ?? true,
      refetchInterval: options.refetchInterval ?? 30_000,
    })),
  });
  return Object.fromEntries(keys.map((k, i) => [k, results[i]?.data])) as Record<string, number | undefined>;
}
