"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Layers, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { GuardedChange } from "@/components/guarded-change";
import { Button, Card, Empty, ErrorBox, Field, Input, Loading, Modal, Table, Td, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { api } from "@/lib/api";
import { P } from "@/lib/perms";
import type { ChangeResponse, Device, Guard, Vlan } from "@/lib/types";

type Pending = { title: string; submit: (g: Guard) => Promise<ChangeResponse> } | null;

/** "10,20-25" -> [{id:10},{id:20}...{id:25}] */
function parseVlanList(s: string, name: string) {
  const ids = new Set<number>();
  for (const part of s.split(/[,\s]+/).filter(Boolean)) {
    const m = part.match(/^(\d+)(?:-(\d+))?$/);
    if (!m) throw new Error(`Noto'g'ri VLAN: ${part}`);
    const a = Number(m[1]);
    const b = m[2] ? Number(m[2]) : a;
    if (a < 2 || b > 4094 || b < a || b - a > 4093) throw new Error(`Noto'g'ri diapazon: ${part}`);
    for (let i = a; i <= b; i++) ids.add(i);
  }
  return [...ids].map((id) => ({ id, name: name || null }));
}

export function VlansTab({ device }: { device: Device }) {
  const qc = useQueryClient();
  const can = useCan();
  const [pending, setPending] = useState<Pending>(null);
  const [creating, setCreating] = useState(false);
  const [ids, setIds] = useState("");
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Vlan | null>(null);
  const [newName, setNewName] = useState("");

  const q = useQuery({ queryKey: ["vlans", device.id], queryFn: () => api.get<Vlan[]>(`/devices/${device.id}/vlans`) });
  const base = `/devices/${device.id}/vlans`;
  const canConf = can(P.DEVICE_CONFIGURE);

  const startCreate = () => {
    try {
      const vlans = parseVlanList(ids, name.trim());
      if (!vlans.length) throw new Error("VLAN kiriting");
      setFormError(null);
      setCreating(false);
      setPending({ title: `VLAN yaratish: ${ids}`, submit: (g) => api.post(base, { vlans, ...g }) });
    } catch (e) {
      setFormError((e as Error).message);
    }
  };

  return (
    <Card
      title="VLAN jadvali"
      bodyClassName="p-0"
      actions={
        <>
          <Button size="sm" variant="secondary" onClick={() => q.refetch()} loading={q.isFetching}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
          {canConf && (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" /> VLAN yaratish
            </Button>
          )}
        </>
      }
    >
      {q.error ? (
        <div className="p-4">
          <ErrorBox error={q.error} />
        </div>
      ) : q.isPending ? (
        <Loading label="Qurilmadan VLANlar o'qilmoqda..." />
      ) : !q.data?.length ? (
        <Empty title="VLAN topilmadi" icon={<Layers />}>Qurilmada VLAN yo&apos;q yoki ma&apos;lumot hali olinmagan.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>ID</Th>
              <Th>Nomi</Th>
              <Th>Holat</Th>
              <Th>Tur</Th>
              <Th>Tagged</Th>
              <Th>Untagged</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((v) => (
              <tr key={v.vlan_id} className="hover:bg-slate-50">
                <Td className="font-mono">{v.vlan_id}</Td>
                <Td>{v.name ?? "—"}</Td>
                <Td className="text-xs">{v.status ?? "—"}</Td>
                <Td className="text-xs">{v.vlan_type ?? "—"}</Td>
                <Td className="max-w-xs text-xs">{v.tagged_ports.join(", ") || "—"}</Td>
                <Td className="max-w-xs text-xs">{v.untagged_ports.join(", ") || "—"}</Td>
                <Td>
                  {canConf && v.vlan_id !== 1 && (
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setRenaming(v);
                          setNewName(v.name ?? "");
                        }}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() =>
                          setPending({
                            title: `VLAN ${v.vlan_id} ni o'chirish`,
                            submit: (g) => api.post(`${base}/delete`, { vlans: String(v.vlan_id), ...g }),
                          })
                        }
                      >
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

      <Modal
        open={creating}
        onClose={() => setCreating(false)}
        title="VLAN yaratish"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreating(false)}>
              Bekor qilish
            </Button>
            <Button onClick={startCreate}>Preview (dry-run)</Button>
          </>
        }
      >
        <div className="space-y-3">
          {formError && <ErrorBox error={formError} />}
          <Field label="VLAN ID(lar)" hint="2–4094. Masalan: 100 yoki 100,110-115">
            <Input value={ids} onChange={(e) => setIds(e.target.value)} autoFocus />
          </Field>
          <Field label="Nomi (ixtiyoriy)">
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={31} />
          </Field>
        </div>
      </Modal>

      <Modal
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title={`VLAN ${renaming?.vlan_id} nomini o'zgartirish`}
        footer={
          <Button
            onClick={() => {
              const v = renaming!;
              setRenaming(null);
              setPending({ title: `VLAN ${v.vlan_id} → ${newName}`, submit: (g) => api.patch(`${base}/${v.vlan_id}`, { name: newName, ...g }) });
            }}
            disabled={!newName.trim()}
          >
            Preview (dry-run)
          </Button>
        }
      >
        <Field label="Yangi nom">
          <Input value={newName} onChange={(e) => setNewName(e.target.value)} maxLength={31} />
        </Field>
      </Modal>

      {pending && (
        <GuardedChange
          open
          title={pending.title}
          submit={pending.submit}
          onClose={() => setPending(null)}
          onApplied={() => qc.invalidateQueries({ queryKey: ["vlans", device.id] })}
        />
      )}
    </Card>
  );
}
