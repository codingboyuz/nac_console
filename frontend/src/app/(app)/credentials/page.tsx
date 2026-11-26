"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderTree, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Select, Table, Td, Textarea, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Credential, CredentialCreate, DeviceGroup, Page } from "@/lib/types";

export default function CredentialsPage() {
  const qc = useQueryClient();
  const can = useCan();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Credential | null>(null);
  const q = useQuery({ queryKey: ["credentials", "all"], queryFn: () => api.get<Page<Credential>>("/credentials", { limit: 500 }) });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/credentials/${id}`),
    onSuccess: () => {
      toast.success("O'chirildi");
      qc.invalidateQueries({ queryKey: ["credentials"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="Credentiallar"
        subtitle="AES-256-GCM bilan shifrlangan. Maxfiy qiymatlar faqat yoziladi — hech qachon qaytarilmaydi."
        actions={
          can(P.CREDENTIALS_WRITE) && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> Credential
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
          ) : !q.data?.items.length ? (
            <Empty title="Credential yo'q" icon={<KeyRound />}>Qurilmalarga ulanish uchun SSH, SNMP yoki Omada API credential yarating. Maxfiy qiymatlar shifrlangan holda saqlanadi.</Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <Th>Nomi</Th>
                  <Th>Tur</Th>
                  <Th>Foydalanuvchi</Th>
                  <Th>Saqlangan maxfiy maydonlar</Th>
                  <Th>Kalit ID</Th>
                  <Th>Yangilangan</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((c) => (
                  <tr key={c.id}>
                    <Td>
                      <div className="font-medium">{c.name}</div>
                      {c.description && <div className="text-xs text-slate-500">{c.description}</div>}
                    </Td>
                    <Td><Badge tone="violet">{c.kind}</Badge></Td>
                    <Td>{c.username ?? "—"}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {c.secret_fields.map((s) => (
                          <Badge key={s}>{s}</Badge>
                        ))}
                      </div>
                    </Td>
                    <Td className="font-mono text-xs">{c.key_id}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtDate(c.updated_at)}</Td>
                    <Td>
                      {can(P.CREDENTIALS_WRITE) && (
                        <div className="flex justify-end gap-1">
                          <Button size="sm" variant="ghost" title="Maxfiy qiymatlarni yangilash" onClick={() => setEditing(c)}>
                            <KeyRound className="h-3.5 w-3.5" />
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => del.mutate(c.id)}>
                            <Trash2 className="h-3.5 w-3.5 text-red-600" />
                          </Button>
                        </div>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <DeviceGroups />
      </div>
      {creating && <CredentialForm onClose={() => setCreating(false)} />}
      {editing && <CredentialForm credential={editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function CredentialForm({ credential, onClose }: { credential?: Credential; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Partial<CredentialCreate>>({
    name: credential?.name ?? "",
    kind: credential?.kind ?? "cli",
    username: credential?.username ?? "",
    description: credential?.description ?? "",
  });
  const set = <K extends keyof CredentialCreate>(k: K, v: CredentialCreate[K]) => setF((s) => ({ ...s, [k]: v }));
  const mut = useMutation({
    mutationFn: () => {
      // Send only filled fields: blank secrets mean "keep the stored value".
      const body = Object.fromEntries(Object.entries(f).filter(([, v]) => v !== "" && v !== undefined && v !== null));
      if (credential) {
        delete body.name;
        delete body.kind;
        return api.patch(`/credentials/${credential.id}`, body);
      }
      return api.post("/credentials", body);
    },
    onSuccess: () => {
      toast.success("Saqlandi");
      qc.invalidateQueries({ queryKey: ["credentials"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const secret = (k: keyof CredentialCreate, label: string, multiline = false) => (
    <Field label={label} hint={credential ? "Bo'sh = o'zgarmaydi" : undefined}>
      {multiline ? (
        <Textarea rows={4} value={(f[k] as string) ?? ""} onChange={(e) => set(k, e.target.value as never)} />
      ) : (
        <Input type="password" autoComplete="new-password" value={(f[k] as string) ?? ""} onChange={(e) => set(k, e.target.value as never)} />
      )}
    </Field>
  );

  return (
    <Modal open onClose={onClose} title={credential ? `Yangilash: ${credential.name}` : "Yangi credential"} footer={<Button onClick={() => mut.mutate()} loading={mut.isPending} disabled={!f.name}>Saqlash</Button>}>
      <div className="space-y-3">
        <Alert tone="info">Saqlangandan keyin maxfiy qiymatlarni ko&apos;rib bo&apos;lmaydi — ular faqat qurilmaga ulanishda deshifrlanadi.</Alert>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Nomi"><Input value={f.name ?? ""} onChange={(e) => set("name", e.target.value)} disabled={!!credential} /></Field>
          <Field label="Tur">
            <Select value={f.kind} onChange={(e) => set("kind", e.target.value as CredentialCreate["kind"])} disabled={!!credential}>
              <option value="cli">cli (SSH/Telnet)</option>
              <option value="snmp_v2c">snmp_v2c</option>
              <option value="snmp_v3">snmp_v3</option>
              <option value="omada_api">omada_api (TP-Link Omada Controller)</option>
            </Select>
          </Field>
        </div>
        <Field label="Tavsif"><Input value={f.description ?? ""} onChange={(e) => set("description", e.target.value)} /></Field>
        {f.kind === "cli" && (
          <>
            <Field label="Foydalanuvchi"><Input value={f.username ?? ""} onChange={(e) => set("username", e.target.value)} /></Field>
            {secret("password", "Parol")}
            {secret("super_password", "super paroli (ixtiyoriy)")}
            {secret("private_key", "SSH private key (ixtiyoriy)", true)}
          </>
        )}
        {f.kind === "omada_api" && (
          <>
            <Alert tone="info">
              Omada Controller → Settings → Platform Integration → Open API: &quot;Client&quot; rejimidagi ilova
              yarating va Client ID / Client Secret hamda Omada ID ni shu yerga kiriting.
            </Alert>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Controller URL">
                <Input value={f.controller_url ?? ""} onChange={(e) => set("controller_url", e.target.value)} placeholder="https://omada.example:8043" disabled={!!credential} />
              </Field>
              <Field label="Omada ID (omadacId)">
                <Input value={f.omadac_id ?? ""} onChange={(e) => set("omadac_id", e.target.value)} disabled={!!credential} />
              </Field>
              <Field label="Site nomi" hint="Bitta site bo'lsa bo'sh qoldiring">
                <Input value={f.omada_site ?? ""} onChange={(e) => set("omada_site", e.target.value)} disabled={!!credential} />
              </Field>
              <Field label="Client ID"><Input value={f.username ?? ""} onChange={(e) => set("username", e.target.value)} /></Field>
            </div>
            {secret("password", "Client Secret")}
          </>
        )}
        {f.kind === "snmp_v2c" && secret("community", "Community")}
        {f.kind === "snmp_v3" && (
          <>
            <Field label="Foydalanuvchi"><Input value={f.username ?? ""} onChange={(e) => set("username", e.target.value)} /></Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Auth protokol">
                <Select value={f.auth_protocol ?? ""} onChange={(e) => set("auth_protocol", (e.target.value || null) as CredentialCreate["auth_protocol"])} disabled={!!credential}>
                  <option value="">—</option>
                  {["SHA", "SHA224", "SHA256", "SHA384", "SHA512", "MD5"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </Select>
              </Field>
              <Field label="Priv protokol">
                <Select value={f.priv_protocol ?? ""} onChange={(e) => set("priv_protocol", (e.target.value || null) as CredentialCreate["priv_protocol"])} disabled={!!credential}>
                  <option value="">—</option>
                  {["AES128", "AES192", "AES256", "DES"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </Select>
              </Field>
            </div>
            {secret("auth_key", "Auth key")}
            {secret("priv_key", "Priv key")}
          </>
        )}
      </div>
    </Modal>
  );
}

function DeviceGroups() {
  const qc = useQueryClient();
  const can = useCan();
  const [name, setName] = useState("");
  const [site, setSite] = useState("");
  const q = useQuery({ queryKey: ["device-groups"], queryFn: () => api.get<Page<DeviceGroup>>("/device-groups", { limit: 500 }) });
  const create = useMutation({
    mutationFn: () => api.post("/device-groups", { name, site: site || null }),
    onSuccess: () => {
      setName("");
      setSite("");
      qc.invalidateQueries({ queryKey: ["device-groups"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/device-groups/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["device-groups"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Card title="Qurilma guruhlari (RBAC scope)" bodyClassName="p-0">
      {can(P.DEVICES_WRITE) && (
        <div className="flex flex-wrap items-end gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <Field label="Nomi"><Input value={name} onChange={(e) => setName(e.target.value)} className="w-56" /></Field>
          <Field label="Sayt"><Input value={site} onChange={(e) => setSite(e.target.value)} className="w-48" /></Field>
          <Button onClick={() => create.mutate()} disabled={!name} loading={create.isPending}>Qo&apos;shish</Button>
        </div>
      )}
      {q.isPending ? (
        <Loading />
      ) : !q.data?.items.length ? (
        <Empty title="Guruh yo'q" icon={<FolderTree />}>Qurilmalarni sayt yoki bo&apos;lim bo&apos;yicha guruhlab, foydalanuvchilarga faqat kerakli guruhlarga ruxsat bering.</Empty>
      ) : (
        <Table>
          <tbody>
            {q.data.items.map((g) => (
              <tr key={g.id}>
                <Td className="font-medium">{g.name}</Td>
                <Td className="text-xs">{g.site ?? "—"}</Td>
                <Td className="font-mono text-xs text-slate-500">{g.id}</Td>
                <Td>
                  {can(P.DEVICES_WRITE) && (
                    <Button size="sm" variant="ghost" onClick={() => del.mutate(g.id)}>
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
  );
}
