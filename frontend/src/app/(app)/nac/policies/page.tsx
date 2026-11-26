"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, History, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Checkbox, Code, Empty, ErrorBox, Field, Input, KV, Loading, Modal, PageHeader, Select, StatusBadge, Table, Td, Textarea, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Decision, Policy, PolicyIn, PolicyVersion, SimulateIn } from "@/lib/types";

const CONDITION_EXAMPLES: Record<string, unknown> = {
  "Har doim": { type: "always" },
  "Ro'yxatdan o'tgan MAC": { type: "mac_registered" },
  "Endpoint guruhi": { type: "endpoint_group_is", group: "staff" },
  "AD guruhi (802.1X)": { type: "user_in_group", group: "Domain Users" },
  "Qurilma turi": { type: "device_type_is", device_type: "printer" },
  "OUI ro'yxati": { type: "oui_in", ouis: ["00:e0:fc"] },
  "Ish vaqti": { type: "time_window", start: "08:00", end: "18:00", days: ["mon", "tue", "wed", "thu", "fri"] },
  "Murakkab (all_of)": { type: "all_of", conditions: [{ type: "auth_method_is", method: "dot1x" }, { type: "user_in_group", group: "IT" }] },
};
const ACTION_EXAMPLES: Record<string, unknown> = {
  "VLAN berish": { type: "assign_vlan", vlan: 100 },
  Ruxsat: { type: "accept" },
  Mehmon: { type: "guest" },
  Karantin: { type: "quarantine" },
  "Rad etish": { type: "reject" },
};

const summarize = (o: Record<string, unknown>): string => {
  const t = String(o.type ?? "?");
  const rest = Object.entries(o)
    .filter(([k]) => k !== "type" && k !== "conditions" && k !== "condition")
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
    .join(" ");
  if (Array.isArray(o.conditions)) return `${t}(${(o.conditions as Record<string, unknown>[]).map(summarize).join(", ")})`;
  if (o.condition) return `not(${summarize(o.condition as Record<string, unknown>)})`;
  return rest ? `${t} ${rest}` : t;
};

export default function PoliciesPage() {
  const qc = useQueryClient();
  const can = useCan();
  const [editing, setEditing] = useState<Policy | "new" | null>(null);
  const [versions, setVersions] = useState<Policy | null>(null);
  const [simulating, setSimulating] = useState(false);
  const q = useQuery({ queryKey: ["policies"], queryFn: () => api.get<Policy[]>("/nac/policies") });
  const del = useMutation({
    mutationFn: (id: string) => api.del(`/nac/policies/${id}`),
    onSuccess: () => {
      toast.success("Siyosat o'chirildi");
      qc.invalidateQueries({ queryKey: ["policies"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const canWrite = can(P.NAC_POLICY_WRITE);

  return (
    <>
      <PageHeader
        title="NAC siyosatlari"
        subtitle="Ustuvorlik bo'yicha tartiblangan; birinchi mos kelgan siyosat qo'llanadi"
        actions={
          <>
            <Button variant="secondary" onClick={() => setSimulating(true)}>
              <FlaskConical className="h-4 w-4" /> Simulyatsiya
            </Button>
            {canWrite && (
              <Button onClick={() => setEditing("new")}>
                <Plus className="h-4 w-4" /> Siyosat
              </Button>
            )}
          </>
        }
      />
      <Card bodyClassName="p-0">
        {q.isPending ? (
          <Loading />
        ) : q.error ? (
          <div className="p-4"><ErrorBox error={q.error} /></div>
        ) : !q.data?.length ? (
          <Empty title="Siyosat yo'q" icon={<ShieldCheck />}>Siyosatlar ustuvorlik bo&apos;yicha tekshiriladi; mos kelgan birinchisi qurilmaning VLANini belgilaydi.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Ustuvorlik</Th>
                <Th>Nomi</Th>
                <Th>Shart</Th>
                <Th>Amal</Th>
                <Th>Holat</Th>
                <Th>Versiya</Th>
                <Th>Yangilangan</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {[...q.data].sort((a, b) => a.priority - b.priority).map((p) => (
                <tr key={p.id} className="hover:bg-slate-50">
                  <Td className="tabular-nums">{p.priority >= 2147483647 ? <span className="text-slate-500">oxirgi (standart)</span> : p.priority}</Td>
                  <Td>
                    <div className="font-medium text-slate-900">{p.name}</div>
                    {p.description && <div className="text-xs text-slate-500">{p.description}</div>}
                  </Td>
                  <Td className="max-w-sm font-mono text-xs">{summarize(p.condition)}</Td>
                  <Td className="font-mono text-xs">{summarize(p.action)}</Td>
                  <Td>
                    {p.enabled ? <Badge tone="green">yoqilgan</Badge> : <Badge>o&apos;chirilgan</Badge>}
                    {p.is_default && <Badge tone="violet" className="ml-1">default</Badge>}
                  </Td>
                  <Td>v{p.version}</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(p.updated_at)}</Td>
                  <Td>
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" title="Versiyalar" onClick={() => setVersions(p)}>
                        <History className="h-3.5 w-3.5" />
                      </Button>
                      {canWrite && (
                        <>
                          <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          {!p.is_default && (
                            <Button size="sm" variant="ghost" onClick={() => confirmDelete(p) && del.mutate(p.id)}>
                              <Trash2 className="h-3.5 w-3.5 text-red-600" />
                            </Button>
                          )}
                        </>
                      )}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {editing && <PolicyEditor policy={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {versions && <VersionsModal policy={versions} onClose={() => setVersions(null)} />}
      {simulating && <SimulateModal onClose={() => setSimulating(false)} />}
    </>
  );
}

// Deletion is versioned and audited server-side; a lightweight inline confirm is enough here.
function confirmDelete(p: Policy) {
  return typeof window !== "undefined" && window.confirm(`"${p.name}" siyosatini o'chirasizmi?`);
}

function PolicyEditor({ policy, onClose }: { policy: Policy | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(policy?.name ?? "");
  const [priority, setPriority] = useState(String(policy?.priority ?? 100));
  const [enabled, setEnabled] = useState(policy?.enabled ?? true);
  const [description, setDescription] = useState(policy?.description ?? "");
  const [condition, setCondition] = useState(JSON.stringify(policy?.condition ?? { type: "always" }, null, 2));
  const [action, setAction] = useState(JSON.stringify(policy?.action ?? { type: "assign_vlan", vlan: 100 }, null, 2));
  const [err, setErr] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (body: PolicyIn) => (policy ? api.put(`/nac/policies/${policy.id}`, body) : api.post("/nac/policies", body)),
    onSuccess: () => {
      toast.success(policy ? "Yangi versiya saqlandi" : "Siyosat yaratildi");
      qc.invalidateQueries({ queryKey: ["policies"] });
      onClose();
    },
    onError: (e) => setErr(errorMessage(e)),
  });

  const save = () => {
    try {
      mut.mutate({
        name,
        priority: Number(priority),
        enabled,
        description: description || null,
        condition: JSON.parse(condition),
        action: JSON.parse(action),
      });
      setErr(null);
    } catch {
      setErr("Shart yoki amal yaroqli JSON emas");
    }
  };

  return (
    <Modal open onClose={onClose} title={policy ? `Tahrirlash: ${policy.name}` : "Yangi siyosat"} wide footer={<Button onClick={save} loading={mut.isPending}>Saqlash</Button>}>
      <div className="space-y-3">
        {err && <Alert tone="danger">{err}</Alert>}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Nomi" className="sm:col-span-2">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Ustuvorlik" hint="Kichik raqam = yuqori ustuvorlik">
            <Input type="number" value={priority} onChange={(e) => setPriority(e.target.value)} />
          </Field>
        </div>
        <Field label="Tavsif">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Checkbox label="Yoqilgan" checked={enabled} onChange={setEnabled} />
        <div className="grid gap-3 lg:grid-cols-2">
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-500">Shart (JSON)</span>
              <Select className="h-7 w-48 text-xs" value="" onChange={(e) => e.target.value && setCondition(JSON.stringify(CONDITION_EXAMPLES[e.target.value], null, 2))}>
                <option value="">Namuna qo&apos;yish...</option>
                {Object.keys(CONDITION_EXAMPLES).map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </Select>
            </div>
            <Textarea rows={12} value={condition} onChange={(e) => setCondition(e.target.value)} />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-slate-500">Amal (JSON)</span>
              <Select className="h-7 w-48 text-xs" value="" onChange={(e) => e.target.value && setAction(JSON.stringify(ACTION_EXAMPLES[e.target.value], null, 2))}>
                <option value="">Namuna qo&apos;yish...</option>
                {Object.keys(ACTION_EXAMPLES).map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </Select>
            </div>
            <Textarea rows={12} value={action} onChange={(e) => setAction(e.target.value)} />
          </div>
        </div>
        <p className="text-xs text-slate-500">
          Shart turlari: always, mac_registered, endpoint_group_is, user_in_group, device_type_is, oui_in, time_window, status_is, auth_method_is,
          site_is, all_of, any_of, not. Amallar: assign_vlan, accept, guest, quarantine, reject.
        </p>
      </div>
    </Modal>
  );
}

function VersionsModal({ policy, onClose }: { policy: Policy; onClose: () => void }) {
  const q = useQuery({ queryKey: ["policy-versions", policy.id], queryFn: () => api.get<PolicyVersion[]>(`/nac/policies/${policy.id}/versions`) });
  return (
    <Modal open onClose={onClose} title={`Versiyalar: ${policy.name}`} wide>
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorBox error={q.error} />
      ) : (
        <div className="space-y-3">
          {[...(q.data ?? [])].reverse().map((v) => (
            <details key={v.version} className="rounded-md border border-slate-200 p-3">
              <summary className="cursor-pointer text-sm">
                v{v.version} — {fmtDate(v.changed_at)}
              </summary>
              <Code className="mt-2">{JSON.stringify(v.snapshot, null, 2)}</Code>
            </details>
          ))}
        </div>
      )}
    </Modal>
  );
}

function SimulateModal({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState<SimulateIn>({ mac: "", auth_method: "mab", user_groups: [] });
  const [groups, setGroups] = useState("");
  const mut = useMutation({
    mutationFn: () => api.post<Decision>("/nac/policies/simulate", { ...f, user_groups: groups.split(",").map((g) => g.trim()).filter(Boolean) }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const set = <K extends keyof SimulateIn>(k: K, v: SimulateIn[K]) => setF((s) => ({ ...s, [k]: v }));
  return (
    <Modal open onClose={onClose} title="Siyosat simulyatsiyasi (dry-run)" wide footer={<Button onClick={() => mut.mutate()} loading={mut.isPending} disabled={!f.mac}>Hisoblash</Button>}>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="MAC *" className="sm:col-span-2">
            <Input className="font-mono" value={f.mac} onChange={(e) => set("mac", e.target.value)} placeholder="00:e0:fc:12:34:56" />
          </Field>
          <Field label="Auth usuli">
            <Select value={f.auth_method} onChange={(e) => set("auth_method", e.target.value as SimulateIn["auth_method"])}>
              <option value="mab">mab</option>
              <option value="dot1x">dot1x</option>
            </Select>
          </Field>
          <Field label="Foydalanuvchi">
            <Input value={f.username ?? ""} onChange={(e) => set("username", e.target.value || null)} />
          </Field>
          <Field label="Foydalanuvchi guruhlari" hint="Vergul bilan" className="sm:col-span-2">
            <Input value={groups} onChange={(e) => setGroups(e.target.value)} />
          </Field>
          <Field label="Endpoint guruhi">
            <Input value={f.endpoint_group ?? ""} onChange={(e) => set("endpoint_group", e.target.value || null)} />
          </Field>
          <Field label="Qurilma turi">
            <Input value={f.device_type ?? ""} onChange={(e) => set("device_type", e.target.value || null)} />
          </Field>
          <Field label="Sayt">
            <Input value={f.site ?? ""} onChange={(e) => set("site", e.target.value || null)} />
          </Field>
          <Field label="Ro'yxatdan o'tgan">
            <Select value={f.registered === null || f.registered === undefined ? "" : String(f.registered)} onChange={(e) => set("registered", e.target.value === "" ? null : e.target.value === "true")}>
              <option value="">bazadan</option>
              <option value="true">ha</option>
              <option value="false">yo&apos;q</option>
            </Select>
          </Field>
        </div>
        <div>
          {mut.data ? (
            <div className="space-y-3">
              <KV
                items={[
                  ["Natija", <StatusBadge key="r" value={mut.data.result.toUpperCase()} />],
                  ["Siyosat", mut.data.policy_name],
                  ["VLAN", mut.data.vlan],
                  ["Endpoint holati", <StatusBadge key="e" value={mut.data.endpoint_status} />],
                ]}
              />
              <div className="text-xs font-medium text-slate-500">RADIUS javob atributlari</div>
              <Code>{JSON.stringify(mut.data.reply, null, 2)}</Code>
            </div>
          ) : (
            <Empty title="Simulyatsiya" icon={<FlaskConical />}>MAC va boshqa parametrlarni kiriting va &quot;Hisoblash&quot;ni bosing — qaysi siyosat ishlashi va natija ko&apos;rsatiladi.</Empty>
          )}
        </div>
      </div>
    </Modal>
  );
}
