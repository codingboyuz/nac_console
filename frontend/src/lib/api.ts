import type { Problem } from "./types";

export class ApiError extends Error {
  status: number;
  problem: Problem;
  constructor(problem: Problem) {
    super(problem.message || problem.detail || problem.title || `HTTP ${problem.status}`);
    this.status = problem.status;
    this.problem = problem;
  }
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Query): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: { query?: Query; headers?: HeadersInit },
): Promise<T> {
  const res = await fetch(`/api/backend${path}${qs(init?.query)}`, {
    method,
    headers: {
      accept: "application/json",
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
      ...init?.headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (res.status === 401 && typeof window !== "undefined") {
    const next = window.location.pathname + window.location.search;
    window.location.assign(`/login?next=${encodeURIComponent(next)}`); // full reload drops cached client state
  }
  if (!res.ok) {
    let problem: Problem = { status: res.status, title: res.statusText };
    try {
      const data = await res.json();
      if (data && typeof data === "object") {
        problem = { ...problem, ...data };
        if (Array.isArray(data.detail)) {
          problem.detail = data.detail
            .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
            .join("; ");
          problem.message = problem.detail;
        }
      }
    } catch {
      /* non-JSON error body */
    }
    const retry = res.headers.get("retry-after");
    if (res.status === 429 && retry) problem.message = `${problem.message ?? "Too many requests"} (${retry}s)`;
    throw new ApiError(problem);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, { query }),
  post: <T>(path: string, body?: unknown, headers?: HeadersInit) =>
    request<T>("POST", path, body ?? {}, { headers }),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const p = e.problem;
    const rid = p.request_id ? ` [${p.request_id}]` : "";
    return `${p.message || p.detail || p.title || `HTTP ${p.status}`}${rid}`;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

/** Idempotency keys; crypto.randomUUID is missing outside secure contexts (plain http on a LAN IP). */
export function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}
