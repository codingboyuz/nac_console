"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import {
  AlertOctagon,
  AlertTriangle,
  BellRing,
  CheckCircle2,
  Cpu,
  Fingerprint,
  Info,
  ListChecks,
  RefreshCw,
  Server,
  ServerCrash,
  ShieldAlert,
  Waypoints,
} from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { Badge, Card, CardLink, Empty, Meter, PageHeader, Stat, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCan, useMe } from "@/hooks/use-me";
import { usePlatforms, vendorTone } from "@/hooks/use-platforms";
import { api } from "@/lib/api";
import { cn, fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Alert, Audit, Device, Job, NacSession, Page } from "@/lib/types";

const count = (path: string, query: Record<string, string>) => () =>
  api.get<Page<unknown>>(path, { ...query, limit: 1 }).then((p) => p.total);

/* Fleet status, in the order the bar draws them; status colours are reserved for state. */
const FLEET = [
  { status: "ONLINE", label: "Onlayn", color: "var(--status-good)", icon: CheckCircle2, text: "text-emerald-700" },
  { status: "AUTH_FAILED", label: "Autentifikatsiya xatosi", color: "var(--status-warning)", icon: ShieldAlert, text: "text-amber-700" },
  { status: "MAINTENANCE", label: "Texnik xizmatda", color: "#8b7ff0", icon: Info, text: "text-violet-700" },
  { status: "OFFLINE", label: "Oflayn", color: "var(--status-serious)", icon: AlertTriangle, text: "text-orange-700" },
  { status: "UNREACHABLE", label: "Yetib bo'lmaydi", color: "var(--status-critical)", icon: ServerCrash, text: "text-red-700" },
  { status: "UNKNOWN", label: "Noma'lum", color: "#cbd5e1", icon: Info, text: "text-slate-500" },
] as const;

const SEVERITIES = [
  { id: "critical", label: "Kritik", icon: AlertOctagon, cls: "text-red-600 bg-red-50" },
  { id: "warning", label: "Ogohlantirish", icon: AlertTriangle, cls: "text-amber-600 bg-amber-50" },
  { id: "info", label: "Ma'lumot", icon: Info, cls: "text-blue-600 bg-blue-50" },
] as const;

function greeting() {
  const h = new Date().getHours();
  return h < 6 ? "Xayrli tun" : h < 12 ? "Xayrli tong" : h < 18 ? "Xayrli kun" : "Xayrli kech";
}

export default function DashboardPage() {
  const me = useMe();
  const can = useCan();
  const canDev = can(P.DEVICES_READ);
  const canAlerts = can(P.ALERTS_READ);
  const canJobs = can(P.JOBS_READ);
  const canNac = can(P.NAC_READ);
  const canMon = can(P.MONITORING_READ);
  const canAudit = can(P.AUDIT_READ);
  const platforms = usePlatforms();

  const devices = useQuery({
    queryKey: ["dash", "devices"],
    queryFn: () => api.get<Page<Device>>("/devices", { limit: 500, sort: "hostname" }),
    enabled: canDev,
    refetchInterval: 60_000,
  });
  const alerts = useQuery({
    queryKey: ["dash", "alerts"],
    queryFn: () => api.get<Page<Alert>>("/alerts", { state: "FIRING", limit: 200, sort: "-last_seen" }),
    enabled: canAlerts,
    refetchInterval: 30_000,
  });
  const jobs = useQuery({
    queryKey: ["dash", "jobs"],
    queryFn: () => api.get<Page<Job>>("/jobs", { limit: 6, sort: "-created_at" }),
    enabled: canJobs,
    refetchInterval: 15_000,
  });
  const running = useQuery({ queryKey: ["dash", "jobs-run"], queryFn: count("/jobs", { status: "RUNNING" }), enabled: canJobs, refetchInterval: 15_000 });
  const endpoints = useQuery({ queryKey: ["dash", "ep"], queryFn: count("/nac/endpoints", {}), enabled: canNac });
  const quarantined = useQuery({ queryKey: ["dash", "ep-q"], queryFn: count("/nac/endpoints", { status: "QUARANTINED" }), enabled: canNac });
  const sessions = useQuery({
    queryKey: ["dash", "sessions"],
    queryFn: () => api.get<NacSession[]>("/nac/sessions", { active: "true" }),
    enabled: canNac,
    refetchInterval: 30_000,
  });
  const audit = useQuery({
    queryKey: ["dash", "audit"],
    queryFn: () => api.get<Page<Audit>>("/logs/audit", { limit: 7 }),
    enabled: canAudit,
    refetchInterval: 30_000,
  });

  const items = useMemo(() => devices.data?.items ?? [], [devices.data]);
  const byStatus = useMemo(() => {
    const m: Record<string, number> = {};
    for (const d of items) m[d.status] = (m[d.status] ?? 0) + 1;
    return m;
  }, [items]);
  const byPlatform = useMemo(() => {
    const m = new Map<string, { platform: string; vendor: string; total: number; online: number; down: number }>();
    for (const d of items) {
      const row = m.get(d.platform) ?? { platform: d.platform, vendor: d.vendor, total: 0, online: 0, down: 0 };
      row.total += 1;
      if (d.status === "ONLINE") row.online += 1;
      if (d.status === "UNREACHABLE" || d.status === "OFFLINE") row.down += 1;
      m.set(d.platform, row);
    }
    return [...m.values()].sort((a, b) => b.total - a.total);
  }, [items]);
  const online = items.filter((d) => d.status === "ONLINE");
  const down = items.filter((d) => d.status === "UNREACHABLE" || d.status === "OFFLINE");
  const total = devices.data?.total ?? 0;
  const availability = items.length ? Math.round((online.length / items.length) * 1000) / 10 : null;

  // Live CPU / memory of the first online devices (latest poll sample).
  const sampled = online.slice(0, 8);
  const latest = useQueries({
    queries: sampled.map((d) => ({
      queryKey: ["dash", "latest", d.id],
      queryFn: () => api.get<Record<string, number | null>>(`/devices/${d.id}/metrics/latest`),
      enabled: canMon,
      refetchInterval: 60_000,
    })),
  });
  const load = sampled
    .map((d, i) => ({ device: d, cpu: latest[i]?.data?.cpu ?? null, memory: latest[i]?.data?.memory ?? null }))
    .sort((a, b) => (b.cpu ?? -1) - (a.cpu ?? -1));

  const firing = alerts.data?.items ?? [];
  const sevCount = (s: string) => firing.filter((a) => a.severity.toLowerCase() === s).length;
  const critical = sevCount("critical");
  const label = (platform: string) => platforms.data?.find((p) => p.platform === platform)?.label ?? platform;
  const deviceName = (id?: string | null) => items.find((d) => d.id === id)?.hostname ?? (id ? id.slice(0, 8) : "—");

  const refreshing = devices.isFetching || alerts.isFetching || jobs.isFetching;

  return (
    <>
      <PageHeader
        title={`${greeting()}, ${me.full_name || me.username}`}
        subtitle="Tarmoq infratuzilmasi va kirishni nazorat qilish holati — real vaqtda"
        actions={
          <span className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-500 shadow-xs">
            <RefreshCw className={cn("h-3.5 w-3.5", refreshing && "animate-spin text-blue-600")} />
            Avtomatik yangilanish · 30 s
          </span>
        }
      />

      {/* KPI row */}
      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        {canDev && (
          <>
            <Stat
              label="Qurilmalar"
              value={devices.data ? total : "—"}
              hint={devices.data ? `${online.length} onlayn` : undefined}
              icon={<Server />}
              href="/devices"
            />
            <Stat
              label="Mavjudlik"
              value={availability !== null ? `${availability}%` : "—"}
              hint="onlayn / jami"
              tone={availability === null ? "default" : availability >= 99 ? "good" : availability >= 90 ? "warn" : "bad"}
              icon={<CheckCircle2 />}
            />
            <Stat
              label="Ishlamayotgan"
              value={devices.data ? down.length : "—"}
              hint="oflayn yoki yetib bo'lmaydi"
              tone={down.length ? "bad" : "good"}
              icon={<ServerCrash />}
              href="/devices?status=UNREACHABLE"
            />
          </>
        )}
        {canAlerts && (
          <Stat
            label="Ochiq ogohlantirishlar"
            value={alerts.data?.total ?? "—"}
            hint={alerts.data ? `${critical} ta kritik` : undefined}
            tone={critical ? "bad" : alerts.data?.total ? "warn" : "good"}
            icon={<BellRing />}
            href="/alerts"
          />
        )}
        {canNac && (
          <>
            <Stat
              label="Faol NAC sessiyalar"
              value={sessions.data?.length ?? "—"}
              hint={endpoints.data !== undefined ? `${endpoints.data} ta endpoint` : undefined}
              icon={<Waypoints />}
              href="/nac/sessions"
            />
            <Stat
              label="Karantinda"
              value={quarantined.data ?? "—"}
              hint="noma'lum qurilmalar"
              tone={quarantined.data ? "warn" : "default"}
              icon={<Fingerprint />}
              href="/nac/endpoints"
            />
          </>
        )}
        {!canNac && canJobs && (
          <Stat label="Bajarilayotgan vazifalar" value={running.data ?? "—"} icon={<ListChecks />} href="/jobs" />
        )}
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        {/* Fleet health */}
        {canDev && (
          <Card
            className="xl:col-span-2"
            title="Qurilmalar holati"
            subtitle={total > items.length ? `Birinchi ${items.length} ta qurilma bo'yicha` : `${total} ta qurilma`}
            actions={<CardLink href="/devices" />}
          >
            {items.length ? (
              <>
                <div className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-slate-100" role="img" aria-label="Qurilmalar holati taqsimoti">
                  {FLEET.filter((f) => byStatus[f.status]).map((f) => (
                    <div
                      key={f.status}
                      className="h-full first:rounded-l-full last:rounded-r-full"
                      style={{ width: `${(byStatus[f.status] / items.length) * 100}%`, background: f.color }}
                      title={`${f.label}: ${byStatus[f.status]}`}
                    />
                  ))}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
                  {FLEET.map((f) => (
                    <div key={f.status} className="flex items-center gap-2 text-xs">
                      <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: f.color }} />
                      <f.icon className={cn("h-3.5 w-3.5", f.text)} />
                      <span className="text-slate-600">{f.label}</span>
                      <span className="ml-auto font-semibold tabular-nums text-slate-900">{byStatus[f.status] ?? 0}</span>
                    </div>
                  ))}
                </div>
                <div className="-mx-4 mt-5 border-t border-slate-200">
                  <Table>
                    <thead>
                      <tr>
                        <Th>Platforma</Th>
                        <Th>Vendor</Th>
                        <Th className="text-right">Jami</Th>
                        <Th className="text-right">Onlayn</Th>
                        <Th className="text-right">Ishlamayapti</Th>
                        <Th>Mavjudlik</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {byPlatform.map((r) => (
                        <tr key={r.platform}>
                          <Td className="font-medium text-slate-900">{label(r.platform)}</Td>
                          <Td>
                            <Badge tone={vendorTone[r.vendor] ?? "slate"}>{r.vendor}</Badge>
                          </Td>
                          <Td className="text-right tabular-nums">{r.total}</Td>
                          <Td className="text-right tabular-nums text-emerald-700">{r.online}</Td>
                          <Td className={cn("text-right tabular-nums", r.down ? "font-semibold text-red-600" : "text-slate-400")}>{r.down}</Td>
                          <Td>
                            <Meter value={(r.online / r.total) * 100} mode="health" />
                          </Td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </>
            ) : devices.isPending ? (
              <Empty>Yuklanmoqda...</Empty>
            ) : (
              <Empty>
                Hali qurilma qo&apos;shilmagan. <CardLink href="/devices">Qurilma qo&apos;shish</CardLink>
              </Empty>
            )}
          </Card>
        )}

        {/* Alerts by severity */}
        {canAlerts && (
          <Card title="Ogohlantirishlar" subtitle="Hozir ochiq, daraja bo'yicha" actions={<CardLink href="/alerts" />} bodyClassName="p-0">
            <div className="grid grid-cols-3 divide-x divide-slate-200 border-b border-slate-200">
              {SEVERITIES.map((s) => (
                <div key={s.id} className="px-2 py-3 text-center">
                  <div className={cn("mx-auto mb-1.5 grid h-7 w-7 place-items-center rounded-md", s.cls)}>
                    <s.icon className="h-3.5 w-3.5" />
                  </div>
                  <div className="text-lg font-semibold tabular-nums text-slate-900">{sevCount(s.id)}</div>
                  <div className="text-[11px] leading-tight text-slate-500">{s.label}</div>
                </div>
              ))}
            </div>
            {firing.length ? (
              <ul className="divide-y divide-slate-100">
                {firing.slice(0, 6).map((a) => (
                  <li key={a.id} className="flex items-start gap-3 px-4 py-2.5">
                    <StatusBadge value={a.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-slate-800">{a.message}</div>
                      <div className="mt-0.5 text-xs text-slate-500">
                        {deviceName(a.device_id)}
                        {a.interface ? ` · ${a.interface}` : ""} · {fmtRelative(a.last_seen)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex items-center gap-2 px-4 py-6 text-sm text-emerald-700">
                <CheckCircle2 className="h-4 w-4" /> Ochiq ogohlantirish yo&apos;q
              </div>
            )}
          </Card>
        )}

        {/* Resource load */}
        {canDev && canMon && (
          <Card title={<span className="flex items-center gap-2"><Cpu className="h-4 w-4 text-slate-400" /> Resurs yuklamasi</span>} subtitle="Oxirgi so'rov natijasi" bodyClassName="p-0">
            {load.length ? (
              <Table>
                <thead>
                  <tr>
                    <Th>Qurilma</Th>
                    <Th>CPU</Th>
                    <Th>Xotira</Th>
                  </tr>
                </thead>
                <tbody>
                  {load.map(({ device, cpu, memory }) => (
                    <tr key={device.id}>
                      <Td>
                        <Link href={`/devices/${device.id}`} className="font-medium text-slate-900 hover:text-blue-600">
                          {device.hostname}
                        </Link>
                        <div className="font-mono text-[11px] text-slate-500">{device.mgmt_ip}</div>
                      </Td>
                      <Td><Meter value={cpu} /></Td>
                      <Td><Meter value={memory} warn={80} crit={92} /></Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <Empty>Onlayn qurilmalar uchun metrikalar yo&apos;q</Empty>
            )}
          </Card>
        )}

        {/* Down devices */}
        {canDev && (
          <Card title="Ishlamayotgan qurilmalar" subtitle="Oflayn yoki yetib bo'lmaydigan" actions={<CardLink href="/devices?status=UNREACHABLE" />} bodyClassName="p-0">
            {down.length ? (
              <Table>
                <tbody>
                  {down.slice(0, 7).map((d) => (
                    <tr key={d.id}>
                      <Td>
                        <Link href={`/devices/${d.id}`} className="font-medium text-slate-900 hover:text-blue-600">
                          {d.hostname}
                        </Link>
                        <div className="font-mono text-[11px] text-slate-500">{d.mgmt_ip}</div>
                      </Td>
                      <Td><StatusBadge value={d.status} /></Td>
                      <Td className="whitespace-nowrap text-right text-xs text-slate-500">{fmtRelative(d.last_seen)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <div className="flex items-center gap-2 px-4 py-6 text-sm text-emerald-700">
                <CheckCircle2 className="h-4 w-4" /> Barcha qurilmalar ishlamoqda
              </div>
            )}
          </Card>
        )}

        {/* Jobs */}
        {canJobs && (
          <Card
            title="So'nggi vazifalar"
            subtitle={running.data ? `${running.data} ta bajarilmoqda` : "Ommaviy o'zgarishlar va backuplar"}
            actions={<CardLink href="/jobs" />}
            bodyClassName="p-0"
          >
            {jobs.data?.items.length ? (
              <Table>
                <tbody>
                  {jobs.data.items.map((j) => (
                    <tr key={j.id}>
                      <Td>
                        <Link href={`/jobs/${j.id}`} className="font-medium text-slate-900 hover:text-blue-600">
                          {j.name}
                        </Link>
                        <div className="text-[11px] text-slate-500">{fmtRelative(j.created_at)}</div>
                      </Td>
                      <Td><StatusBadge value={j.status} /></Td>
                      <Td className="w-28">
                        <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                          <div className="h-full rounded-full bg-blue-600" style={{ width: `${j.progress}%` }} />
                        </div>
                        <div className="mt-0.5 text-right text-[11px] tabular-nums text-slate-500">{j.progress}%</div>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <Empty>Vazifalar yo&apos;q</Empty>
            )}
          </Card>
        )}

        {/* Audit trail */}
        {canAudit && (
          <Card className="xl:col-span-3" title="Oxirgi amallar" subtitle="Audit jurnali — kim, qachon, nima qildi" actions={<CardLink href="/audit" />} bodyClassName="p-0">
            {audit.data?.items.length ? (
              <Table>
                <thead>
                  <tr>
                    <Th>Vaqt</Th>
                    <Th>Foydalanuvchi</Th>
                    <Th>Amal</Th>
                    <Th>Qurilma</Th>
                    <Th>Toifa</Th>
                    <Th>Natija</Th>
                  </tr>
                </thead>
                <tbody>
                  {audit.data.items.map((a) => (
                    <tr key={a.id}>
                      <Td className="whitespace-nowrap text-xs text-slate-500">{fmtRelative(a.timestamp)}</Td>
                      <Td className="font-medium text-slate-900">{a.username ?? "tizim"}</Td>
                      <Td className="max-w-md truncate font-mono text-xs">{a.operation}</Td>
                      <Td>{a.device_id ? deviceName(a.device_id) : "—"}</Td>
                      <Td>{a.command_category ? <StatusBadge value={a.command_category} /> : "—"}</Td>
                      <Td>
                        <Badge tone={a.success ? "green" : "red"} dot>
                          {a.success ? "Muvaffaqiyatli" : "Xato"}
                        </Badge>
                      </Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : (
              <Empty>Audit yozuvlari yo&apos;q</Empty>
            )}
          </Card>
        )}
      </div>
    </>
  );
}
