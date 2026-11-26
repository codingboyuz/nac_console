"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, PlugZap, RefreshCw, Trash2, Wrench } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { DeviceForm } from "@/components/device-form";
import { Badge, Button, Card, Code, KV, Modal } from "@/components/ui";
import { Can, useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtUptime } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Device, TestConnection } from "@/lib/types";

export function OverviewTab({ device }: { device: Device }) {
  const qc = useQueryClient();
  const router = useRouter();
  const can = useCan();
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [conn, setConn] = useState<TestConnection | null>(null);

  const latest = useQuery({
    queryKey: ["metrics-latest", device.id],
    queryFn: () => api.get<Record<string, number | null>>(`/devices/${device.id}/metrics/latest`),
    enabled: can(P.MONITORING_READ),
  });

  // Per-interface samples arrive as "metric:Interface"; the card shows device-level values only.
  const deviceMetrics = Object.entries(latest.data ?? {}).filter(([k]) => !k.includes(":"));

  const test = useMutation({
    mutationFn: () => api.post<TestConnection>(`/devices/${device.id}/test-connection`),
    onSuccess: (r) => {
      setConn(r);
      toast[r.reachable ? "success" : "error"](r.reachable ? "Ulanish muvaffaqiyatli" : "Qurilmaga ulanib bo'lmadi");
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const poll = useMutation({
    mutationFn: () => api.post(`/devices/${device.id}/poll`),
    onSuccess: () => {
      toast.success("Metrikalar yangilandi");
      qc.invalidateQueries({ queryKey: ["metrics-latest", device.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const maintenance = useMutation({
    mutationFn: () =>
      api.patch<Device>(`/devices/${device.id}`, { status: device.status === "MAINTENANCE" ? "UNKNOWN" : "MAINTENANCE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  const del = useMutation({
    mutationFn: () => api.del(`/devices/${device.id}`),
    onSuccess: () => {
      toast.success("Qurilma o'chirildi");
      qc.invalidateQueries({ queryKey: ["devices"] });
      router.push("/devices");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="grid gap-6 xl:grid-cols-3">
      <Card
        className="xl:col-span-2"
        title="Ma'lumotlar"
        actions={
          <>
            <Can perm={P.DEVICE_TEST}>
              <Button size="sm" onClick={() => test.mutate()} loading={test.isPending}>
                <PlugZap className="h-3.5 w-3.5" /> Ulanishni tekshirish
              </Button>
            </Can>
            <Can perm={P.DEVICES_WRITE}>
              <Button size="sm" variant="secondary" onClick={() => maintenance.mutate()} loading={maintenance.isPending}>
                <Wrench className="h-3.5 w-3.5" /> {device.status === "MAINTENANCE" ? "Texnik xizmatdan chiqarish" : "Texnik xizmat rejimi"}
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>
                <Pencil className="h-3.5 w-3.5" /> Tahrirlash
              </Button>
            </Can>
            <Can perm={P.DEVICES_DELETE}>
              <Button size="sm" variant="danger" onClick={() => setDeleting(true)}>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </Can>
          </>
        }
      >
        <KV
          items={[
            ["Hostname", device.hostname],
            ["Boshqaruv IP", <span key="ip" className="font-mono">{device.mgmt_ip}</span>],
            ["Vendor", device.vendor],
            ["Model", device.model],
            ["Oila", device.family],
            ["VRP versiya", device.vrp_version],
            ["Protokol / port", `${device.protocol} / ${device.port}`],
            ["SNMP port", device.snmp_port],
            ["Boshqaruv interfeysi", device.mgmt_interface],
            ["Boshqaruv VLAN", device.mgmt_vlan],
            ["Uplinklar", device.uplink_interfaces.join(", ") || "—"],
            ["Sayt", device.site],
            ["Timeout", `${device.timeout}s`],
            ["super talab", device.requires_super ? "ha" : "yo'q"],
            ["Oxirgi ko'rilgan", fmtDate(device.last_seen)],
            ["Yaratilgan", fmtDate(device.created_at)],
            [
              "Teglar",
              <span key="t" className="flex flex-wrap justify-end gap-1">
                {device.tags.map((t) => (
                  <Badge key={t}>{t}</Badge>
                ))}
              </span>,
            ],
            ["Tavsif", device.description],
          ]}
        />
        {Object.keys(device.capabilities ?? {}).length > 0 && (
          <div className="mt-4">
            <div className="mb-1 text-xs font-medium text-slate-500">Imkoniyatlar</div>
            <Code>{JSON.stringify(device.capabilities, null, 2)}</Code>
          </div>
        )}
      </Card>

      <div className="space-y-6">
        <Can perm={P.MONITORING_READ}>
          <Card
            title="Joriy metrikalar"
            actions={
              <Button size="sm" variant="secondary" onClick={() => poll.mutate()} loading={poll.isPending}>
                <RefreshCw className="h-3.5 w-3.5" /> So&apos;rash
              </Button>
            }
          >
            {deviceMetrics.length ? (
              <KV
                items={deviceMetrics.map(([k, v]) => [
                  k,
                  v === null ? "—" : k === "uptime" ? fmtUptime(v) : ["cpu", "memory"].includes(k) ? `${v.toFixed(1)}%` : String(v),
                ])}
              />
            ) : (
              <p className="text-sm text-slate-500">Hali metrika yig&apos;ilmagan.</p>
            )}
          </Card>
        </Can>
        {conn && (
          <Card title="Ulanish natijasi">
            <KV
              items={[
                ["Yetib boriladi", conn.reachable ? "ha" : "yo'q"],
                ["Oila", conn.family],
                ["Model", conn.model],
                ["VRP", conn.vrp_version],
                ["Dasturiy ta'minot", conn.software_version],
                ["Uptime", fmtUptime(conn.uptime_seconds)],
              ]}
            />
            {conn.security_warning && <p className="mt-2 text-xs text-amber-600">{conn.security_warning}</p>}
          </Card>
        )}
      </div>

      {editing && <DeviceForm open device={device} onClose={() => setEditing(false)} />}
      <Modal
        open={deleting}
        onClose={() => setDeleting(false)}
        title="Qurilmani o'chirish"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleting(false)}>
              Bekor qilish
            </Button>
            <Button variant="danger" onClick={() => del.mutate()} loading={del.isPending}>
              O&apos;chirish
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-700">
          <b>{device.hostname}</b> inventardan o&apos;chiriladi. Qurilma konfiguratsiyasi o&apos;zgarmaydi.
        </p>
      </Modal>
    </div>
  );
}
