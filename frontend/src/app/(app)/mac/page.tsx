"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Download, MapPin, SearchX } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Select, Table, Tabs, Td, Th } from "@/components/ui";
import { useAllDevices, useDeviceNames } from "@/hooks/use-devices";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Arp, MacEntry, MacLocation } from "@/lib/types";

export default function MacPage() {
  const [tab, setTab] = useState<"mac" | "arp">("mac");
  return (
    <>
      <PageHeader title="MAC / ARP qidiruv" subtitle="Yig'ilgan MAC jadvallari va ARP bo'yicha qidirish, portni aniqlash" />
      <Tabs
        tabs={[
          { id: "mac", label: "MAC jadvali" },
          { id: "arp", label: "ARP (IP ↔ MAC)" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "mac" ? <MacSearch /> : <ArpSearch />}
    </>
  );
}

function CollectButton({ kind }: { kind: "mac-addresses" | "arp" }) {
  const qc = useQueryClient();
  const devices = useAllDevices();
  const [deviceId, setDeviceId] = useState("");
  const mut = useMutation({
    mutationFn: () => api.post(`/${kind}/collect/${deviceId}`),
    onSuccess: () => {
      toast.success("Yig'ish yakunlandi");
      qc.invalidateQueries({ queryKey: [kind] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <div className="flex gap-2">
      <Select className="w-56" value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
        <option value="">— qurilma —</option>
        {devices.data?.items.map((d) => (
          <option key={d.id} value={d.id}>{d.hostname}</option>
        ))}
      </Select>
      <Button variant="secondary" disabled={!deviceId} loading={mut.isPending} onClick={() => mut.mutate()}>
        <Download className="h-4 w-4" /> Hozir yig&apos;ish
      </Button>
    </div>
  );
}

function MacSearch() {
  const name = useDeviceNames();
  const can = useCan();
  const params = useSearchParams();
  const [form, setForm] = useState({ mac: params.get("q") ?? "", vlan: "", interface: "" });
  const [query, setQuery] = useState(form);
  const [locate, setLocate] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<MacEntry | null>(null);

  const q = useQuery({
    queryKey: ["mac-addresses", query],
    queryFn: () => api.get<MacEntry[]>("/mac-addresses", { mac: query.mac, vlan: query.vlan, interface: query.interface, limit: 500 }),
  });
  const loc = useQuery({
    queryKey: ["mac-locate", locate],
    queryFn: () => api.get<MacLocation[]>(`/mac-addresses/${encodeURIComponent(locate!)}/locate`),
    enabled: !!locate,
  });
  const block = useMutation({
    mutationFn: (e: MacEntry) => api.post("/mac-addresses/block", { mac: e.mac, vlan: e.vlan, device_id: e.device_id }),
    onSuccess: () => {
      toast.success("MAC blackhole qilindi");
      setBlocking(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setQuery(form);
  };

  return (
    <Card bodyClassName="p-0">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
        <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
          <Field label="MAC">
            <Input className="w-48 font-mono" value={form.mac} onChange={(e) => setForm({ ...form, mac: e.target.value })} placeholder="00e0-fc12-3456" />
          </Field>
          <Field label="VLAN">
            <Input className="w-24" type="number" value={form.vlan} onChange={(e) => setForm({ ...form, vlan: e.target.value })} />
          </Field>
          <Field label="Interfeys">
            <Input className="w-44" value={form.interface} onChange={(e) => setForm({ ...form, interface: e.target.value })} />
          </Field>
          <Button type="submit">Qidirish</Button>
        </form>
        {can(P.DEVICE_SHOW) && <CollectButton kind="mac-addresses" />}
      </div>
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <div className="p-4"><ErrorBox error={q.error} /></div>
      ) : !q.data?.length ? (
        <Empty title="Natija yo'q" icon={<SearchX />}>MAC manzil, VLAN yoki interfeys bo&apos;yicha qidiring. Jadval bo&apos;sh bo&apos;lsa, qurilmani tanlab &quot;Hozir yig&apos;ish&quot;ni bosing.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>MAC</Th>
              <Th>VLAN</Th>
              <Th>Qurilma</Th>
              <Th>Interfeys</Th>
              <Th>Tur</Th>
              <Th>Ko&apos;chishlar</Th>
              <Th>Oxirgi</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((m, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <Td className="font-mono text-xs">{m.mac}</Td>
                <Td>{m.vlan ?? "—"}</Td>
                <Td>
                  <Link href={`/devices/${m.device_id}`} className="text-blue-600 hover:underline">{name(m.device_id)}</Link>
                </Td>
                <Td className="font-mono text-xs">
                  {m.interface ?? "—"} {m.is_edge && <Badge tone="sky">edge</Badge>}
                </Td>
                <Td className="text-xs">{m.entry_type}</Td>
                <Td className={m.moves > 3 ? "text-amber-600" : ""}>{m.moves}</Td>
                <Td className="whitespace-nowrap text-xs">{fmtRelative(m.last_seen)}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" title="Portni aniqlash" onClick={() => setLocate(m.mac)}>
                      <MapPin className="h-3.5 w-3.5" />
                    </Button>
                    {can(P.DEVICE_CONFIGURE) && m.vlan !== null && (
                      <Button size="sm" variant="ghost" title="Blackhole" onClick={() => setBlocking(m)}>
                        <Ban className="h-3.5 w-3.5 text-red-600" />
                      </Button>
                    )}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <Modal open={!!locate} onClose={() => setLocate(null)} title={`MAC joylashuvi: ${locate}`}>
        {loc.isPending ? (
          <Loading />
        ) : loc.error ? (
          <ErrorBox error={loc.error} />
        ) : !loc.data?.length ? (
          <Empty title="Topilmadi" icon={<SearchX />}>Bu MAC manzil hech qaysi switch portida ko&apos;rinmadi.</Empty>
        ) : (
          <div className="space-y-2">
            {loc.data.map((l, i) => (
              <div key={i} className="rounded-md border border-slate-200 p-3 text-sm">
                <div className="font-medium text-slate-900">
                  {l.hostname} — <span className="font-mono">{l.interface ?? "?"}</span> {l.is_edge && <Badge tone="sky">edge port</Badge>}
                </div>
                <div className="text-xs text-slate-500">VLAN {l.vlan ?? "—"} · {fmtRelative(l.last_seen)}</div>
              </div>
            ))}
          </div>
        )}
      </Modal>

      <Modal
        open={!!blocking}
        onClose={() => setBlocking(null)}
        title="MAC blackhole"
        footer={
          <Button variant="danger" loading={block.isPending} onClick={() => blocking && block.mutate(blocking)}>
            Blackhole qilish
          </Button>
        }
      >
        <div className="space-y-3 text-sm">
          <Alert tone="warn">
            Bu faqat qulaylik uchun vosita, xavfsizlik nazorati EMAS (MAC osonlik bilan soxtalashtiriladi). Endpointni bloklash uchun NAC
            bo&apos;limidan foydalaning.
          </Alert>
          <p>
            <span className="font-mono">{blocking?.mac}</span> VLAN {blocking?.vlan} — {name(blocking?.device_id)}
          </p>
        </div>
      </Modal>
    </Card>
  );
}

function ArpSearch() {
  const name = useDeviceNames();
  const can = useCan();
  const [form, setForm] = useState({ ip: "", mac: "" });
  const [query, setQuery] = useState(form);
  const q = useQuery({
    queryKey: ["arp", query],
    queryFn: () => api.get<Arp[]>("/arp", { ip: query.ip, mac: query.mac, limit: 500 }),
  });
  return (
    <Card bodyClassName="p-0">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(form);
          }}
          className="flex flex-wrap items-end gap-2"
        >
          <Field label="IP">
            <Input className="w-44 font-mono" value={form.ip} onChange={(e) => setForm({ ...form, ip: e.target.value })} />
          </Field>
          <Field label="MAC">
            <Input className="w-48 font-mono" value={form.mac} onChange={(e) => setForm({ ...form, mac: e.target.value })} />
          </Field>
          <Button type="submit">Qidirish</Button>
        </form>
        {can(P.DEVICE_SHOW) && <CollectButton kind="arp" />}
      </div>
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <div className="p-4"><ErrorBox error={q.error} /></div>
      ) : !q.data?.length ? (
        <Empty title="Natija yo'q" icon={<SearchX />}>IP yoki MAC bo&apos;yicha qidiring. ARP jadvali qurilmalardan yig&apos;ilgandan keyin to&apos;ladi.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>IP</Th>
              <Th>MAC</Th>
              <Th>VLAN</Th>
              <Th>Interfeys</Th>
              <Th>Qurilma</Th>
              <Th>Tur</Th>
              <Th>VPN</Th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a, i) => (
              <tr key={i}>
                <Td className="font-mono text-xs">{a.ip}</Td>
                <Td className="font-mono text-xs">{a.mac}</Td>
                <Td>{a.vlan ?? "—"}</Td>
                <Td className="font-mono text-xs">{a.interface ?? "—"}</Td>
                <Td>{name(a.device_id)}</Td>
                <Td className="text-xs">{a.entry_type}</Td>
                <Td className="text-xs">{a.vpn}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
