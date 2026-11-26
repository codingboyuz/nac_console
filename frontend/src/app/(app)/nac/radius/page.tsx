"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileCode2, Network, Plus, Trash2, Wand2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { JobForm } from "@/components/job-form";
import { Alert, Badge, Button, Card, Checkbox, Code, DiffView, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { useAllDevices } from "@/hooks/use-devices";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { P } from "@/lib/perms";
import type { ProvisionIn, ProvisionPreview, RadiusServer, RadiusServerIn } from "@/lib/types";

export default function RadiusPage() {
  const qc = useQueryClient();
  const can = useCan();
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["radius-servers"], queryFn: () => api.get<RadiusServer[]>("/radius/servers") });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/radius/servers/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["radius-servers"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="RADIUS serverlar"
        subtitle="Switchlar uchun RADIUS (FreeRADIUS) konfiguratsiyasi va 802.1X/MAB provisioning"
        actions={
          can(P.RADIUS_WRITE) && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> Server
            </Button>
          )
        }
      />
      <div className="space-y-6">
        <Card bodyClassName="p-0">
          {q.isPending ? (
            <Loading />
          ) : q.error ? (
            <div className="p-4"><ErrorBox error={q.error} /></div>
          ) : !q.data?.length ? (
            <Empty title="RADIUS server qo'shilmagan" icon={<Network />}>FreeRADIUS server manzilini qo&apos;shing — keyin switchlar uchun 802.1X/MAB sozlamalari shu server asosida yaratiladi.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Nomi</Th>
                  <Th>Host</Th>
                  <Th>Auth / Acct / CoA</Th>
                  <Th>Timeout / retries</Th>
                  <Th>Secret</Th>
                  <Th>Holat</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {q.data.map((s) => (
                  <tr key={s.id}>
                    <Td className="font-medium">{s.name}</Td>
                    <Td className="font-mono text-xs">{s.host}</Td>
                    <Td className="font-mono text-xs">{s.auth_port} / {s.acct_port} / {s.coa_port}</Td>
                    <Td className="text-xs">{s.timeout}s / {s.retries}</Td>
                    <Td>{s.shared_secret_set ? <Badge tone="green">o&apos;rnatilgan</Badge> : <Badge tone="red">yo&apos;q</Badge>}</Td>
                    <Td>{s.enabled ? <Badge tone="green">yoqilgan</Badge> : <Badge>o&apos;chirilgan</Badge>}</Td>
                    <Td>
                      {can(P.RADIUS_WRITE) && (
                        <Button size="sm" variant="ghost" onClick={() => del.mutate(s.id)}>
                          <Trash2 className="h-3.5 w-3.5 text-red-600" />
                        </Button>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        {can(P.NAC_WRITE) && q.data && q.data.length > 0 && <Provisioning servers={q.data} />}
      </div>
      {creating && <ServerForm onClose={() => setCreating(false)} />}
    </>
  );
}

function ServerForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<RadiusServerIn>({ name: "", host: "", shared_secret: "", auth_port: 1812, acct_port: 1813, coa_port: 3799, timeout: 5, retries: 3, enabled: true });
  const set = <K extends keyof RadiusServerIn>(k: K, v: RadiusServerIn[K]) => setF((s) => ({ ...s, [k]: v }));
  const mut = useMutation({
    mutationFn: () => api.post("/radius/servers", f),
    onSuccess: () => {
      toast.success("Server qo'shildi");
      qc.invalidateQueries({ queryKey: ["radius-servers"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Modal open onClose={onClose} title="Yangi RADIUS server" footer={<Button onClick={() => mut.mutate()} loading={mut.isPending} disabled={!f.name || !f.host || !f.shared_secret}>Saqlash</Button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nomi"><Input value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
        <Field label="Host"><Input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="192.0.2.5" /></Field>
        <Field label="Shared secret" hint="Faqat yoziladi, qaytarib ko'rsatilmaydi" className="sm:col-span-2">
          <Input type="password" autoComplete="new-password" value={f.shared_secret} onChange={(e) => set("shared_secret", e.target.value)} />
        </Field>
        <Field label="Auth port"><Input type="number" value={f.auth_port} onChange={(e) => set("auth_port", Number(e.target.value))} /></Field>
        <Field label="Acct port"><Input type="number" value={f.acct_port} onChange={(e) => set("acct_port", Number(e.target.value))} /></Field>
        <Field label="CoA port"><Input type="number" value={f.coa_port} onChange={(e) => set("coa_port", Number(e.target.value))} /></Field>
        <Field label="Timeout (s)"><Input type="number" value={f.timeout} onChange={(e) => set("timeout", Number(e.target.value))} /></Field>
        <Field label="Retries"><Input type="number" value={f.retries} onChange={(e) => set("retries", Number(e.target.value))} /></Field>
        <div className="flex items-end"><Checkbox label="Yoqilgan" checked={!!f.enabled} onChange={(v) => set("enabled", v)} /></div>
      </div>
    </Modal>
  );
}

function Provisioning({ servers }: { servers: RadiusServer[] }) {
  const devices = useAllDevices();
  const [deviceId, setDeviceId] = useState("");
  const [serverId, setServerId] = useState(servers[0]?.id ?? "");
  const [mode, setMode] = useState<ProvisionIn["mode"]>("both");
  const [interfaces, setInterfaces] = useState("");
  const [guest, setGuest] = useState("");
  const [quarantine, setQuarantine] = useState("");
  const [deploy, setDeploy] = useState(false);

  const body = (): ProvisionIn => ({
    device_id: deviceId,
    radius_server_id: serverId,
    mode,
    interfaces: interfaces.split(/[,\s]+/).filter(Boolean),
    guest_vlan: guest ? Number(guest) : null,
    quarantine_vlan: quarantine ? Number(quarantine) : null,
  });
  const preview = useMutation({
    mutationFn: () => api.post<ProvisionPreview>("/nac/provisioning/preview", body()),
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card
      title={<span className="flex items-center gap-2"><Wand2 className="h-4 w-4" /> Switch provisioning (AAA / RADIUS / 802.1X / MAB)</span>}
    >
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-3">
          <Field label="Qurilma">
            <Select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
              <option value="">— tanlang —</option>
              {devices.data?.items.map((d) => (
                <option key={d.id} value={d.id}>{d.hostname}</option>
              ))}
            </Select>
          </Field>
          <Field label="RADIUS server">
            <Select value={serverId} onChange={(e) => setServerId(e.target.value)}>
              {servers.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Rejim">
            <Select value={mode} onChange={(e) => setMode(e.target.value as ProvisionIn["mode"])}>
              <option value="both">802.1X + MAB</option>
              <option value="dot1x">Faqat 802.1X</option>
              <option value="mab">Faqat MAB</option>
            </Select>
          </Field>
          <Field label="Access portlar" hint="Vergul bilan. Uplinklar avtomatik chiqarib tashlanadi">
            <Input value={interfaces} onChange={(e) => setInterfaces(e.target.value)} placeholder="GigabitEthernet0/0/1, GigabitEthernet0/0/2" />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Guest VLAN"><Input type="number" value={guest} onChange={(e) => setGuest(e.target.value)} /></Field>
            <Field label="Karantin VLAN"><Input type="number" value={quarantine} onChange={(e) => setQuarantine(e.target.value)} /></Field>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => preview.mutate()} loading={preview.isPending} disabled={!deviceId || !serverId}>Preview</Button>
            {preview.data && (
              <Button variant="warning" onClick={() => setDeploy(true)}>Job orqali joylash</Button>
            )}
          </div>
        </div>
        <div className="space-y-3 xl:col-span-2">
          <Alert tone="warn">Generatsiya qilingan buyruqlar VRP versiyasiga bog&apos;liq. Joylashdan oldin laboratoriyada tekshiring.</Alert>
          {preview.data ? (
            <>
              <div className="text-xs font-medium text-slate-500">Buyruqlar</div>
              <Code>{preview.data.lines.join("\n")}</Code>
              <div className="text-xs font-medium text-slate-500">Joriy konfiguratsiyaga nisbatan diff</div>
              <DiffView diff={preview.data.diff} />
            </>
          ) : (
            <Empty title="Preview olinmagan" icon={<FileCode2 />}>Qurilma va portlarni tanlab, switchga yuboriladigan sozlamalarni oldindan ko&apos;ring.</Empty>
          )}
        </div>
      </div>
      {deploy && (
        <JobForm
          open
          onClose={() => setDeploy(false)}
          preset={{ type: "nac.provision", target_devices: [deviceId], params: { ...body(), device_id: undefined }, name: "NAC provisioning" }}
        />
      )}
    </Card>
  );
}
