"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, CheckCircle2, Clock, Fingerprint, HelpCircle, ShieldAlert } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, Checkbox, Empty, ErrorBox, Field, Input, SearchInput, KV, Loading, Modal, PageHeader, Pagination, Select, StatStrip, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCounts } from "@/hooks/use-counts";
import { useDeviceNames } from "@/hooks/use-devices";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Endpoint, EndpointUpdate, Page } from "@/lib/types";

const LIMIT = 50;
const STATUSES = ["AUTHORIZED", "UNKNOWN", "QUARANTINED", "BLOCKED", "PENDING"];

export default function EndpointsPage() {
  const name = useDeviceNames();
  const [status, setStatus] = useState("");
  const [mac, setMac] = useState("");
  const [macQuery, setMacQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Endpoint | null>(null);

  const { data, error, isPending } = useQuery({
    queryKey: ["endpoints", { status, macQuery, offset }],
    queryFn: () => api.get<Page<Endpoint>>("/nac/endpoints", { status, mac: macQuery, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  const counts = useCounts("/nac/endpoints", "status", STATUSES);
  const pick = (v: string) => () => {
    setStatus((cur) => (cur === v ? "" : v));
    setOffset(0);
  };

  const search = (e: FormEvent) => {
    e.preventDefault();
    setMacQuery(mac.trim());
    setOffset(0);
  };

  return (
    <>
      <PageHeader title="Endpointlar" subtitle="Tarmoqqa ulangan qurilmalar, profillash va avtorizatsiya holati" />
      <StatStrip
        items={[
          { label: "Jami", value: counts[""] ?? "—", icon: <Fingerprint />, onClick: pick(""), active: !status },
          { label: "Avtorizatsiyalangan", value: counts.AUTHORIZED ?? "—", tone: "good", icon: <CheckCircle2 />, onClick: pick("AUTHORIZED"), active: status === "AUTHORIZED" },
          { label: "Karantinda", value: counts.QUARANTINED ?? "—", tone: counts.QUARANTINED ? "warn" : "muted", icon: <ShieldAlert />, onClick: pick("QUARANTINED"), active: status === "QUARANTINED" },
          { label: "Bloklangan", value: counts.BLOCKED ?? "—", tone: counts.BLOCKED ? "bad" : "muted", icon: <Ban />, onClick: pick("BLOCKED"), active: status === "BLOCKED" },
          { label: "Kutilmoqda", value: counts.PENDING ?? "—", tone: "muted", icon: <Clock />, onClick: pick("PENDING"), active: status === "PENDING" },
          { label: "Noma'lum", value: counts.UNKNOWN ?? "—", tone: "muted", icon: <HelpCircle />, onClick: pick("UNKNOWN"), active: status === "UNKNOWN" },
        ]}
      />
      <Card bodyClassName="p-0">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <form onSubmit={search} className="flex gap-2">
            <SearchInput className="w-60 font-mono" placeholder="MAC manzil" value={mac} onChange={(e) => setMac(e.target.value)} />
            <Button type="submit" variant="secondary">Qidirish</Button>
          </form>
          <Select className="w-44" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="">Barcha holatlar</option>
            {STATUSES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </div>
        {error ? (
          <div className="p-4"><ErrorBox error={error} /></div>
        ) : isPending ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty title={status || macQuery ? "Mos endpoint topilmadi" : "Hali endpoint yo'q"} icon={<Fingerprint />}>
            {status || macQuery
              ? "Qidiruv yoki holat filtrini o'zgartirib ko'ring."
              : "Switchlar RADIUS orqali 802.1X/MAB so'rov yuborganda, ulangan qurilmalar shu yerda avtomatik paydo bo'ladi."}
          </Empty>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>MAC</Th>
                  <Th>IP</Th>
                  <Th>Hostname</Th>
                  <Th>Tur / OS</Th>
                  <Th>Holat</Th>
                  <Th>Guruh</Th>
                  <Th>Switch / port</Th>
                  <Th>VLAN</Th>
                  <Th>Oxirgi</Th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelected(e)}>
                    <Td className="font-mono text-xs text-blue-600">{e.mac}</Td>
                    <Td className="font-mono text-xs">{e.ip ?? "—"}</Td>
                    <Td>{e.hostname ?? "—"}</Td>
                    <Td className="text-xs">
                      {e.device_type ?? "—"}
                      {e.os && <div className="text-slate-500">{e.os}</div>}
                    </Td>
                    <Td>
                      <StatusBadge value={e.status} />
                      {e.registered && <Badge tone="sky" className="ml-1">reg</Badge>}
                    </Td>
                    <Td className="text-xs">{e.endpoint_group ?? "—"}</Td>
                    <Td className="text-xs">
                      {name(e.switch_id)}
                      {e.interface && <div className="font-mono text-slate-500">{e.interface}</div>}
                    </Td>
                    <Td>{e.vlan ?? "—"}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtRelative(e.last_seen)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
      {selected && <EndpointModal endpoint={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function EndpointModal({ endpoint: e, onClose }: { endpoint: Endpoint; onClose: () => void }) {
  const qc = useQueryClient();
  const can = useCan();
  const name = useDeviceNames();
  const [form, setForm] = useState<EndpointUpdate>({
    registered: e.registered,
    endpoint_group: e.endpoint_group,
    device_type: e.device_type,
    description: e.description,
  });
  const done = (msg: string) => {
    toast.success(msg);
    qc.invalidateQueries({ queryKey: ["endpoints"] });
    onClose();
  };
  const mac = encodeURIComponent(e.mac);
  const save = useMutation({
    mutationFn: () => api.put(`/nac/endpoints/${mac}`, form),
    onSuccess: () => done("Saqlandi"),
    onError: (err) => toast.error(errorMessage(err)),
  });
  const block = useMutation({
    mutationFn: (b: boolean) => api.post(`/nac/endpoints/${mac}/${b ? "block" : "unblock"}`),
    onSuccess: (_, b) => done(b ? "Bloklandi va sessiyalar uzildi" : "Blokdan chiqarildi"),
    onError: (err) => toast.error(errorMessage(err)),
  });
  const canWrite = can(P.NAC_WRITE);

  return (
    <Modal
      open
      onClose={onClose}
      title={<span className="font-mono">{e.mac}</span>}
      wide
      footer={
        canWrite && (
          <>
            {e.status === "BLOCKED" ? (
              <Button variant="secondary" onClick={() => block.mutate(false)} loading={block.isPending}>Blokdan chiqarish</Button>
            ) : (
              <Button variant="danger" onClick={() => block.mutate(true)} loading={block.isPending}>Bloklash</Button>
            )}
            <Button onClick={() => save.mutate()} loading={save.isPending}>Saqlash</Button>
          </>
        )
      }
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <KV
          items={[
            ["Holat", <StatusBadge key="s" value={e.status} />],
            ["Avtorizatsiya", e.authorization_state],
            ["IP", e.ip],
            ["Hostname", e.hostname],
            ["Vendor", e.vendor],
            ["OS", e.os],
            ["Foydalanuvchi", e.user],
            ["Switch", name(e.switch_id)],
            ["Port", e.interface],
            ["VLAN", e.vlan],
            ["Profil ishonchi", `${Math.round(e.profile_confidence * 100)}%`],
            ["Manba", e.source],
            ["Birinchi", fmtDate(e.first_seen)],
            ["Oxirgi", fmtDate(e.last_seen)],
          ]}
        />
        <div className="space-y-3">
          <Checkbox label="Ro'yxatdan o'tgan" checked={!!form.registered} onChange={(v) => setForm({ ...form, registered: v })} disabled={!canWrite} />
          <Field label="Endpoint guruhi">
            <Input value={form.endpoint_group ?? ""} onChange={(ev) => setForm({ ...form, endpoint_group: ev.target.value || null })} disabled={!canWrite} />
          </Field>
          <Field label="Qurilma turi">
            <Input value={form.device_type ?? ""} onChange={(ev) => setForm({ ...form, device_type: ev.target.value || null })} disabled={!canWrite} />
          </Field>
          <Field label="Tavsif">
            <Input value={form.description ?? ""} onChange={(ev) => setForm({ ...form, description: ev.target.value || null })} disabled={!canWrite} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}
