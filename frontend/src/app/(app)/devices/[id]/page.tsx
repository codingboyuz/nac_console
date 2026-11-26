"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Archive,
  ArrowLeft,
  Cable,
  Clock,
  Cpu,
  Info,
  KeySquare,
  Layers,
  MapPin,
  Plug,
  Route,
  Server,
  SquareTerminal,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Alert, Badge, ErrorBox, Loading, statusToneOf, Tabs } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { usePlatforms, vendorTone } from "@/hooks/use-platforms";
import { api } from "@/lib/api";
import { cn, fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Device } from "@/lib/types";
import { BackupsTab } from "./_tabs/backups";
import { HostKeysTab } from "./_tabs/host-keys";
import { InterfacesTab } from "./_tabs/interfaces";
import { MonitoringTab } from "./_tabs/monitoring";
import { OverviewTab } from "./_tabs/overview";
import { RoutingTab } from "./_tabs/routing";
import { TerminalTab } from "./_tabs/terminal";
import { VlansTab } from "./_tabs/vlans";

type TabId = "overview" | "interfaces" | "vlans" | "terminal" | "backups" | "monitoring" | "routing" | "hostkeys";

const STATUS_LABEL: Record<string, string> = {
  ONLINE: "Onlayn",
  OFFLINE: "Oflayn",
  UNREACHABLE: "Yetib bo'lmaydi",
  AUTH_FAILED: "Autentifikatsiya xatosi",
  MAINTENANCE: "Texnik xizmatda",
  UNKNOWN: "Holati noma'lum",
};

const statusRing: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-600 ring-emerald-200",
  red: "bg-red-50 text-red-600 ring-red-200",
  amber: "bg-amber-50 text-amber-600 ring-amber-200",
  violet: "bg-violet-50 text-violet-600 ring-violet-200",
  sky: "bg-blue-50 text-blue-600 ring-blue-200",
  slate: "bg-slate-50 text-slate-500 ring-slate-200",
};
const statusDot: Record<string, string> = {
  green: "bg-emerald-500",
  red: "bg-red-500",
  amber: "bg-amber-500",
  violet: "bg-violet-500",
  sky: "bg-blue-500",
  slate: "bg-slate-400",
};

function Fact({ icon, label, children }: { icon: ReactNode; label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-400 [&>svg]:h-3.5 [&>svg]:w-3.5">
        {icon}
        {label}
      </div>
      <div className="mt-1 truncate text-sm font-medium text-slate-900">{children}</div>
    </div>
  );
}

export default function DevicePage() {
  const { id } = useParams<{ id: string }>();
  const can = useCan();
  const [tab, setTab] = useState<TabId>("overview");
  const platforms = usePlatforms();
  const { data: device, error, isPending } = useQuery({
    queryKey: ["devices", id],
    queryFn: () => api.get<Device>(`/devices/${id}`),
  });

  const platformInfo = platforms.data?.find((p) => p.platform === device?.platform);
  if (isPending) return <Loading />;
  if (error || !device) return <ErrorBox error={error ?? "Topilmadi"} />;

  const tone = statusToneOf(device.status);
  const label = (icon: ReactNode, text: string) => (
    <span className="inline-flex items-center gap-1.5 [&>svg]:h-4 [&>svg]:w-4">
      {icon}
      {text}
    </span>
  );
  const tabs: { id: TabId; label: ReactNode; show: boolean }[] = [
    { id: "overview", label: label(<Info />, "Umumiy"), show: true },
    { id: "interfaces", label: label(<Cable />, "Interfeyslar"), show: true },
    { id: "vlans", label: label(<Layers />, "VLAN"), show: true },
    { id: "terminal", label: label(<SquareTerminal />, "Terminal"), show: can(P.DEVICE_SHOW) },
    { id: "backups", label: label(<Archive />, "Backup / Drift"), show: can(P.BACKUPS_READ) },
    { id: "monitoring", label: label(<Activity />, "Monitoring"), show: can(P.MONITORING_READ) },
    { id: "routing", label: label(<Route />, "STP / Marshrutlash"), show: can(P.DEVICE_SHOW) },
    { id: "hostkeys", label: label(<KeySquare />, "SSH kalitlar"), show: true },
  ];

  return (
    <>
      <Link href="/devices" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-900">
        <ArrowLeft className="h-3.5 w-3.5" /> Qurilmalar ro&apos;yxati
      </Link>

      <section className="mb-5 rounded-lg border border-slate-200 bg-white shadow-xs">
        <div className="flex flex-wrap items-center gap-4 p-5">
          <div className={cn("grid h-12 w-12 shrink-0 place-items-center rounded-xl ring-1 ring-inset", statusRing[tone])}>
            <Server className="h-6 w-6" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-xl font-semibold tracking-tight text-slate-900">{device.hostname}</h1>
              <Badge tone={vendorTone[device.vendor] ?? "slate"}>{device.vendor}</Badge>
              {device.family !== "UNKNOWN" && <Badge tone="violet">{device.family}</Badge>}
              {device.tags.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
            <div className="mt-1 flex items-center gap-2 text-sm">
              <span className={cn("h-2 w-2 rounded-full", statusDot[tone], tone === "green" && "animate-pulse")} />
              <span className="font-medium text-slate-700">{STATUS_LABEL[device.status] ?? device.status}</span>
              <span className="text-slate-300">·</span>
              <span className="text-slate-500">{platformInfo?.label ?? device.platform}</span>
              {device.description && (
                <>
                  <span className="text-slate-300">·</span>
                  <span className="truncate text-slate-500">{device.description}</span>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 border-t border-slate-200 bg-slate-50/50 px-5 py-4 sm:grid-cols-3 lg:grid-cols-6">
          <Fact icon={<Plug />} label="Boshqaruv">
            <span className="font-mono">
              {device.mgmt_ip}
              <span className="text-slate-400">:{device.port}</span>
            </span>
          </Fact>
          <Fact icon={<Cable />} label="Protokol">
            {device.protocol.toUpperCase()}
          </Fact>
          <Fact icon={<Cpu />} label="Model">
            {device.model ?? "—"}
          </Fact>
          <Fact icon={<Info />} label="Dasturiy ta'minot">
            {device.vrp_version ?? "—"}
          </Fact>
          <Fact icon={<MapPin />} label="Sayt">
            {device.site ?? "—"}
          </Fact>
          <Fact icon={<Clock />} label="Oxirgi aloqa">
            {fmtRelative(device.last_seen)}
          </Fact>
        </div>
      </section>

      {device.security_warning && (
        <div className="mb-4">
          <Alert tone="warn">{device.security_warning}</Alert>
        </div>
      )}
      <Tabs tabs={tabs.filter((t) => t.show)} value={tab} onChange={setTab} />
      {tab === "overview" && <OverviewTab device={device} />}
      {tab === "interfaces" && <InterfacesTab device={device} />}
      {tab === "vlans" && <VlansTab device={device} />}
      {tab === "terminal" && <TerminalTab device={device} />}
      {tab === "backups" && <BackupsTab device={device} />}
      {tab === "monitoring" && <MonitoringTab device={device} />}
      {tab === "routing" && <RoutingTab device={device} />}
      {tab === "hostkeys" && <HostKeysTab device={device} />}
    </>
  );
}
