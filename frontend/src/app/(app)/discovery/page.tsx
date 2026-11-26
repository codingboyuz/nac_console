"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, KV, Loading, Modal, PageHeader, Select, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { isHuawei, usePlatforms } from "@/hooks/use-platforms";
import { api, errorMessage } from "@/lib/api";
import { P } from "@/lib/perms";
import type { Credential, Device, Page, Platform } from "@/lib/types";

type Proposal = {
  id: string;
  ip: string;
  sys_name: string | null;
  family: string | null;
  is_huawei: boolean;
  ssh_banner: string | null;
  status: string;
  adopted_device_id: string | null;
};
type Run = { id: string; cidr: string; status: string; hosts_scanned: number; hosts_found: number; error: string | null; devices: Proposal[] };

const ACTIVE = new Set(["PENDING", "RUNNING"]);

export default function DiscoveryPage() {
  const qc = useQueryClient();
  const can = useCan();
  const [cidr, setCidr] = useState("");
  const [snmp, setSnmp] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [adopting, setAdopting] = useState<Proposal | null>(null);

  const creds = useQuery({
    queryKey: ["credentials", "all"],
    queryFn: () => api.get<Page<Credential>>("/credentials", { limit: 500 }),
  });
  const run = useQuery({
    queryKey: ["discovery", runId],
    queryFn: () => api.get<Run>(`/discovery/${runId}`),
    enabled: !!runId,
    refetchInterval: (q) => (q.state.data && !ACTIVE.has(q.state.data.status.toUpperCase()) ? false : 3000),
  });
  const start = useMutation({
    mutationFn: () => api.post<{ run_id: string }>("/discovery", { cidr, snmp_credential_ids: snmp }),
    onSuccess: (r) => {
      setRunId(r.run_id);
      toast.success("Discovery boshlandi");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const ignore = useMutation({
    mutationFn: (id: string) => api.post(`/discovery/devices/${id}/ignore`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["discovery", runId] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    start.mutate();
  };
  const snmpCreds = creds.data?.items.filter((c) => c.kind !== "cli") ?? [];
  const r = run.data;

  return (
    <>
      <PageHeader title="Discovery" subtitle="Ruxsat etilgan subnetni skanerlash (ICMP → SNMP → SSH banner). Natijalar faqat taklif — inventarga qo'lda qo'shiladi." />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6">
          <Card title="Yangi skanerlash">
            <form onSubmit={submit} className="space-y-3">
              <Field label="CIDR" hint="Faqat backendda ruxsat etilgan tarmoqlar ichida">
                <Input value={cidr} onChange={(e) => setCidr(e.target.value)} placeholder="192.0.2.0/24" required />
              </Field>
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">SNMP credentiallar</div>
                {snmpCreds.length ? (
                  snmpCreds.map((c) => (
                    <label key={c.id} className="flex items-center gap-2 text-sm text-slate-700">
                      <input
                        type="checkbox"
                        className="accent-blue-600"
                        checked={snmp.includes(c.id)}
                        onChange={() => setSnmp((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id]))}
                      />
                      {c.name} <span className="text-xs text-slate-500">({c.kind})</span>
                    </label>
                  ))
                ) : (
                  <p className="text-xs text-slate-500">SNMP credential yo&apos;q</p>
                )}
              </div>
              <Button type="submit" loading={start.isPending}>
                <Radar className="h-4 w-4" /> Skanerlash
              </Button>
            </form>
          </Card>
          {r && (
            <Card title="Holat">
              <KV
                items={[
                  ["CIDR", r.cidr],
                  ["Holat", <StatusBadge key="s" value={r.status} />],
                  ["Skanerlangan", r.hosts_scanned],
                  ["Topilgan", r.hosts_found],
                ]}
              />
              {r.error && <p className="mt-2 text-xs text-red-600">{r.error}</p>}
            </Card>
          )}
        </div>
        <Card title="Topilgan qurilmalar" className="xl:col-span-2" bodyClassName="p-0">
          {!runId ? (
            <Empty title="Skanerlash boshlanmagan" icon={<Radar />}>Chapdagi formada subnet kiriting. Topilgan qurilmalar shu yerda taklif sifatida chiqadi va siz ularni inventarga qo&apos;shasiz.</Empty>
          ) : run.isPending ? (
            <Loading />
          ) : run.error ? (
            <div className="p-4"><ErrorBox error={run.error} /></div>
          ) : !r?.devices.length ? (
            <Empty icon={<Radar />} title={r && ACTIVE.has(r.status.toUpperCase()) ? "Skanerlanmoqda..." : "Qurilma topilmadi"}>{r && ACTIVE.has(r.status.toUpperCase()) ? "Natijalar tayyor bo'lishi bilan shu yerda paydo bo'ladi." : "Bu subnetda javob bergan tarmoq qurilmasi yo'q."}</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>IP</Th>
                  <Th>sysName</Th>
                  <Th>Oila</Th>
                  <Th>SSH banner</Th>
                  <Th>Holat</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {r.devices.map((d) => (
                  <tr key={d.id}>
                    <Td className="font-mono text-xs">{d.ip}</Td>
                    <Td>
                      {d.sys_name ?? "—"} {d.is_huawei && <Badge tone="violet">Huawei</Badge>}
                    </Td>
                    <Td>{d.family ?? "—"}</Td>
                    <Td className="max-w-xs truncate font-mono text-xs">{d.ssh_banner ?? "—"}</Td>
                    <Td><StatusBadge value={d.status} /></Td>
                    <Td>
                      {d.adopted_device_id ? (
                        <Link href={`/devices/${d.adopted_device_id}`} className="text-xs text-blue-600 hover:underline">Qurilma</Link>
                      ) : (
                        can(P.DISCOVERY_ADOPT) &&
                        d.status !== "IGNORED" && (
                          <div className="flex justify-end gap-1">
                            {can(P.DEVICES_WRITE) && (
                              <Button size="sm" onClick={() => setAdopting(d)}>Qo&apos;shish</Button>
                            )}
                            <Button size="sm" variant="secondary" onClick={() => ignore.mutate(d.id)}>E&apos;tiborsiz</Button>
                          </div>
                        )
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
      {adopting && <AdoptModal proposal={adopting} credentials={creds.data?.items ?? []} onClose={() => setAdopting(null)} onDone={() => {
        setAdopting(null);
        qc.invalidateQueries({ queryKey: ["discovery", runId] });
        qc.invalidateQueries({ queryKey: ["devices"] });
      }} />}
    </>
  );
}

function AdoptModal({ proposal, credentials, onClose, onDone }: { proposal: Proposal; credentials: Credential[]; onClose: () => void; onDone: () => void }) {
  const clean = (proposal.sys_name ?? "").replace(/[^A-Za-z0-9_.-]/g, "-").replace(/^[^A-Za-z0-9]+/, "");
  const [hostname, setHostname] = useState(clean || `dev-${proposal.ip.replace(/[.:]/g, "-")}`);
  const [credentialId, setCredentialId] = useState("");
  const [family, setFamily] = useState(proposal.family && ["S", "CE", "AR"].includes(proposal.family) ? proposal.family : "UNKNOWN");
  const platforms = usePlatforms();
  const [platform, setPlatform] = useState<Platform>("huawei_vrp");
  const mut = useMutation({
    mutationFn: () =>
      api.post<Device>(`/discovery/devices/${proposal.id}/adopt`, {
        hostname,
        mgmt_ip: proposal.ip,
        credential_id: credentialId || null,
        platform,
        family: isHuawei(platform) ? family : "UNKNOWN",
        protocol: "ssh",
        snmp_port: 161,
        timeout: 30,
        tags: [],
        uplink_interfaces: [],
        requires_super: false,
        allow_insecure_protocol: false,
      }),
    onSuccess: (d) => {
      toast.success(`${d.hostname} inventarga qo'shildi`);
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Modal open onClose={onClose} title={`Inventarga qo'shish: ${proposal.ip}`} footer={<Button loading={mut.isPending} onClick={() => mut.mutate()}>Qo&apos;shish</Button>}>
      <div className="space-y-3">
        <Field label="Hostname">
          <Input value={hostname} onChange={(e) => setHostname(e.target.value)} />
        </Field>
        <Field label="Platforma">
          <Select value={platform} onChange={(e) => setPlatform(e.target.value as Platform)}>
            {platforms.data?.filter((p) => p.available).map((p) => (
              <option key={p.platform} value={p.platform}>
                {p.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Oila (Huawei)">
          <Select value={family} onChange={(e) => setFamily(e.target.value)}>
            {["UNKNOWN", "S", "CE", "AR"].map((f) => (
              <option key={f}>{f}</option>
            ))}
          </Select>
        </Field>
        <Field label="CLI credential">
          <Select value={credentialId} onChange={(e) => setCredentialId(e.target.value)}>
            <option value="">—</option>
            {credentials.filter((c) => c.kind === "cli").map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}
