import clsx, { type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Later classes win over conflicting earlier ones (e.g. a caller's `w-40` over a base `w-full`). */
export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

export function fmtDate(v?: string | null): string {
  if (!v) return "—";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return v;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function fmtRelative(v?: string | null): string {
  if (!v) return "—";
  const diff = (Date.now() - new Date(v).getTime()) / 1000;
  if (Number.isNaN(diff)) return v;
  if (diff < 60) return `${Math.max(0, Math.round(diff))} soniya oldin`;
  if (diff < 3600) return `${Math.round(diff / 60)} daqiqa oldin`;
  if (diff < 86400) return `${Math.round(diff / 3600)} soat oldin`;
  return `${Math.round(diff / 86400)} kun oldin`;
}

export function fmtBytes(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${units[i]}`;
}

export function fmtUptime(s?: number | null): string {
  if (s === null || s === undefined) return "—";
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${d} kun ${h} soat ${m} daq`;
}

export function shortId(id?: string | null): string {
  return id ? id.slice(0, 8) : "—";
}
