"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, Radio, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, ErrorBox, Loading, PageHeader, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useDeviceNames } from "@/hooks/use-devices";
import { useBackendSocket } from "@/hooks/use-ws";
import { api } from "@/lib/api";
import type { Finding } from "@/lib/types";

type WsMetric = { type: "metric"; device_id: string; metric: string; value: number | null; labels?: Record<string, string>; timestamp?: string };
type Row = { device_id: string; values: Record<string, number | null>; at: number };

const COLUMNS = ["cpu", "memory", "temperature", "uptime", "fan_ok", "power_ok"];

export default function MonitoringPage() {
  const name = useDeviceNames();
  const [rows, setRows] = useState<Record<string, Row>>({});
  const live = useBackendSocket<WsMetric>("/ws/monitoring", (m) => {
    if (m.type !== "metric" || m.labels?.interface) return; // device-level metrics only
    setRows((prev) => {
      const cur = prev[m.device_id] ?? { device_id: m.device_id, values: {}, at: 0 };
      return { ...prev, [m.device_id]: { ...cur, values: { ...cur.values, [m.metric]: m.value }, at: Date.now() } };
    });
  });
  const anomalies = useQuery({ queryKey: ["anomalies"], queryFn: () => api.get<Finding[]>("/anomalies"), refetchInterval: 60_000 });

  const list = Object.values(rows).sort((a, b) => name(a.device_id).localeCompare(name(b.device_id)));
  const fmt = (k: string, v: number | null | undefined) => {
    if (v === null || v === undefined) return "—";
    if (k === "cpu" || k === "memory") return `${v.toFixed(1)}%`;
    if (k === "temperature") return `${v.toFixed(0)}°C`;
    if (k === "uptime") return `${Math.floor(v / 86400)} kun`;
    if (k.endsWith("_ok")) return v ? "OK" : "XATO";
    return String(v);
  };
  const tone = (k: string, v: number | null | undefined) =>
    v === null || v === undefined ? "" : (k === "cpu" || k === "memory") && v > 85 ? "text-red-600" : k.endsWith("_ok") && !v ? "text-red-600" : "";

  return (
    <>
      <PageHeader
        title="Monitoring"
        subtitle="Jonli metrikalar oqimi va L2 anomaliyalar"
        actions={
          <span className={`inline-flex items-center gap-1 text-xs ${live ? "text-emerald-600" : "text-slate-500"}`}>
            <Radio className={`h-3.5 w-3.5 ${live ? "animate-pulse" : ""}`} /> {live ? "WebSocket ulangan" : "ulanmoqda..."}
          </span>
        }
      />
      <div className="space-y-6">
        <Card title="Jonli metrikalar" bodyClassName="p-0">
          {!list.length ? (
            <Empty title="Metrikalar kutilmoqda" icon={<Activity />}>Poller yangi qiymatlarni yuborishi bilan ular shu yerda jonli chiqadi. Qurilma sahifasidagi &quot;So&apos;rash&quot; tugmasi bilan darhol yig&apos;ish mumkin.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Qurilma</Th>
                  {COLUMNS.map((c) => (
                    <Th key={c}>{c}</Th>
                  ))}
                  <Th>Yangilangan</Th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => (
                  <tr key={r.device_id}>
                    <Td>
                      <Link href={`/devices/${r.device_id}`} className="text-blue-600 hover:underline">{name(r.device_id)}</Link>
                    </Td>
                    {COLUMNS.map((c) => (
                      <Td key={c} className={`tabular-nums ${tone(c, r.values[c])}`}>{fmt(c, r.values[c])}</Td>
                    ))}
                    <Td className="text-xs text-slate-500">{new Date(r.at).toLocaleTimeString()}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title="Anomaliyalar (takroriy IP, MAC flapping, ARP / binding nomuvofiqligi)" bodyClassName="p-0">
          {anomalies.isPending ? (
            <Loading />
          ) : anomalies.error ? (
            <div className="p-4"><ErrorBox error={anomalies.error} /></div>
          ) : !anomalies.data?.length ? (
            <Empty title="Anomaliya aniqlanmadi" icon={<ShieldCheck />}>Takroriy IP, MAC flapping yoki ARP/DHCP nomuvofiqligi topilmadi.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Daraja</Th>
                  <Th>Tur</Th>
                  <Th>Kalit</Th>
                  <Th>Tafsilot</Th>
                </tr>
              </thead>
              <tbody>
                {anomalies.data.map((f, i) => (
                  <tr key={i}>
                    <Td><StatusBadge value={f.severity} /></Td>
                    <Td className="font-mono text-xs">{f.kind}</Td>
                    <Td className="font-mono text-xs">{f.key}</Td>
                    <Td className="text-sm">{f.detail}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
