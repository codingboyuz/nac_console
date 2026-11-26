"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { MetricChart } from "@/components/metric-chart";
import { Card, ErrorBox, Field, Input, Loading, Select } from "@/components/ui";
import { api } from "@/lib/api";
import type { Device, MetricPoint } from "@/lib/types";

const METRICS = [
  { id: "cpu", label: "CPU (%)", iface: false },
  { id: "memory", label: "Xotira (%)", iface: false },
  { id: "temperature", label: "Harorat (°C)", iface: false },
  { id: "if_in_bps", label: "Kiruvchi trafik (bps)", iface: true },
  { id: "if_out_bps", label: "Chiquvchi trafik (bps)", iface: true },
  { id: "if_util_pct", label: "Port yuklamasi (%)", iface: true },
  { id: "if_in_errors_ps", label: "Kiruvchi xatolar (/s)", iface: true },
  { id: "if_discards_ps", label: "Discardlar (/s)", iface: true },
];
const RANGES = [
  { h: 1, label: "1 soat" },
  { h: 6, label: "6 soat" },
  { h: 24, label: "24 soat" },
  { h: 168, label: "7 kun" },
];

export function MonitoringTab({ device }: { device: Device }) {
  const [hours, setHours] = useState(1);
  const [iface, setIface] = useState("");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Davr">
          <Select value={hours} onChange={(e) => setHours(Number(e.target.value))} className="w-36">
            {RANGES.map((r) => (
              <option key={r.h} value={r.h}>
                {r.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Interfeys (port metrikalari uchun)">
          <Input value={iface} onChange={(e) => setIface(e.target.value)} placeholder="GigabitEthernet0/0/1" className="w-64" />
        </Field>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {METRICS.filter((m) => !m.iface || iface).map((m) => (
          <MetricCard key={m.id} deviceId={device.id} metric={m.id} label={m.label} hours={hours} iface={m.iface ? iface : undefined} />
        ))}
      </div>
    </div>
  );
}

function MetricCard({ deviceId, metric, label, hours, iface }: { deviceId: string; metric: string; label: string; hours: number; iface?: string }) {
  const q = useQuery({
    queryKey: ["metrics", deviceId, metric, hours, iface],
    queryFn: () =>
      api.get<MetricPoint[]>(`/devices/${deviceId}/metrics`, {
        metric,
        interface: iface,
        since: new Date(Date.now() - hours * 3600_000).toISOString(),
      }),
    refetchInterval: 60_000,
  });
  return (
    <Card title={label}>
      {q.isPending ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : <MetricChart points={q.data!} />}
    </Card>
  );
}
