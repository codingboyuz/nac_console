"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Cable, Eye, Power, RefreshCw, Settings2 } from "lucide-react";
import { useState } from "react";
import { GuardedChange } from "@/components/guarded-change";
import { Badge, Button, Card, Code, Empty, ErrorBox, Field, Input, Loading, Modal, Select, Table, Td, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { isHuawei } from "@/hooks/use-platforms";
import { api } from "@/lib/api";
import { P } from "@/lib/perms";
import type { ChangeResponse, Device, Guard, Interface } from "@/lib/types";

/** Interface names contain slashes; the backend route uses {interface:path}. */
export const ifPath = (name: string) => name.split("/").map(encodeURIComponent).join("/");

type Pending = { title: string; submit: (g: Guard) => Promise<ChangeResponse> } | null;

export function InterfacesTab({ device }: { device: Device }) {
  const qc = useQueryClient();
  const can = useCan();
  const [refresh, setRefresh] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [editing, setEditing] = useState<Interface | null>(null);
  const [detail, setDetail] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["interfaces", device.id, refresh],
    queryFn: () => api.get<Interface[]>(`/devices/${device.id}/interfaces`, { refresh }),
  });

  const base = `/devices/${device.id}/interfaces`;
  const canConf = can(P.DEVICE_CONFIGURE);

  return (
    <Card
      title="Interfeyslar"
      bodyClassName="p-0"
      actions={
        <Button size="sm" variant="secondary" loading={q.isFetching} onClick={() => (refresh ? q.refetch() : setRefresh(true))}>
          <RefreshCw className="h-3.5 w-3.5" /> Qurilmadan yangilash
        </Button>
      }
    >
      {q.error ? (
        <div className="p-4">
          <ErrorBox error={q.error} />
        </div>
      ) : q.isPending ? (
        <Loading />
      ) : !q.data?.length ? (
        <Empty title="Interfeyslar yuklanmagan" icon={<Cable />}>Portlar ro&apos;yxatini olish uchun yuqoridagi &quot;Qurilmadan yangilash&quot; tugmasini bosing.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Nomi</Th>
              <Th>Admin</Th>
              <Th>Oper</Th>
              <Th>Tezlik</Th>
              <Th>Rejim / PVID</Th>
              <Th>Xatolar (in/out/CRC)</Th>
              <Th>Tavsif</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((i) => (
              <tr key={i.name} className="hover:bg-slate-50">
                <Td className="font-mono text-xs">
                  {i.name} {i.is_uplink && <Badge tone="sky">uplink</Badge>}
                </Td>
                <Td>{i.admin_up === null ? "—" : <Badge tone={i.admin_up ? "green" : "red"}>{i.admin_up ? "up" : "down"}</Badge>}</Td>
                <Td>{i.oper_up === null ? "—" : <Badge tone={i.oper_up ? "green" : "slate"}>{i.oper_up ? "up" : "down"}</Badge>}</Td>
                <Td className="text-xs">
                  {i.speed_mbps ? `${i.speed_mbps} Mbps` : "—"} {i.duplex ?? ""}
                </Td>
                <Td className="text-xs">
                  {i.link_type ?? "—"} {i.pvid ? `/ ${i.pvid}` : ""}
                </Td>
                <Td className="text-xs tabular-nums">
                  {[i.input_errors, i.output_errors, i.crc_errors].map((v) => v ?? "—").join(" / ")}
                </Td>
                <Td className="max-w-[16rem] truncate text-xs">{i.description ?? ""}</Td>
                <Td>
                  <div className="flex justify-end gap-1">
                    {can(P.DEVICE_SHOW) && (
                      <Button size="sm" variant="ghost" title="Batafsil" onClick={() => setDetail(i.name)}>
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                    )}
                    {canConf && (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          title={i.admin_up ? "shutdown" : "undo shutdown"}
                          onClick={() =>
                            setPending({
                              title: `${i.name}: ${i.admin_up ? "shutdown" : "undo shutdown"}`,
                              submit: (g) => api.put(`${base}/${ifPath(i.name)}/admin-state`, { enabled: !i.admin_up, ...g }),
                            })
                          }
                        >
                          <Power className={i.admin_up ? "h-3.5 w-3.5 text-red-600" : "h-3.5 w-3.5 text-emerald-600"} />
                        </Button>
                        <Button size="sm" variant="ghost" title="Sozlash" onClick={() => setEditing(i)}>
                          <Settings2 className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      {editing && (
        <InterfaceEditor
          iface={editing}
          onClose={() => setEditing(null)}
          onSubmit={(p) => {
            setEditing(null);
            setPending(p);
          }}
          base={base}
          hybrid={isHuawei(device.platform)}
        />
      )}
      {detail && <InterfaceDetail deviceId={device.id} name={detail} onClose={() => setDetail(null)} />}
      {pending && (
        <GuardedChange
          open
          title={pending.title}
          submit={pending.submit}
          onClose={() => setPending(null)}
          onApplied={() => qc.invalidateQueries({ queryKey: ["interfaces", device.id] })}
        />
      )}
    </Card>
  );
}

function InterfaceEditor({
  iface,
  base,
  hybrid,
  onClose,
  onSubmit,
}: {
  iface: Interface;
  base: string;
  hybrid: boolean;
  onClose: () => void;
  onSubmit: (p: NonNullable<Pending>) => void;
}) {
  const [kind, setKind] = useState<"vlan" | "description" | "speed" | "trunk">("vlan");
  const [mode, setMode] = useState(iface.link_type ?? "access");
  const [accessVlan, setAccessVlan] = useState(iface.pvid ? String(iface.pvid) : "");
  const [pvid, setPvid] = useState("");
  const [trunkVlans, setTrunkVlans] = useState("");
  const [tagged, setTagged] = useState("");
  const [untagged, setUntagged] = useState("");
  const [desc, setDesc] = useState(iface.description ?? "");
  const [auto, setAuto] = useState(true);
  const [speed, setSpeed] = useState("");
  const [duplex, setDuplex] = useState("");
  const [add, setAdd] = useState("");
  const [remove, setRemove] = useState("");
  const path = `${base}/${ifPath(iface.name)}`;
  const num = (s: string) => (s ? Number(s) : null);

  const go = () => {
    if (kind === "vlan")
      onSubmit({
        title: `${iface.name}: port VLAN (${mode})`,
        submit: (g) =>
          api.put(`${path}/vlan`, {
            mode,
            access_vlan: mode === "access" ? num(accessVlan) : null,
            pvid: mode !== "access" ? num(pvid) : null,
            trunk_vlans: mode === "trunk" ? trunkVlans || null : null,
            tagged: mode === "hybrid" ? tagged || null : null,
            untagged: mode === "hybrid" ? untagged || null : null,
            ...g,
          }),
      });
    if (kind === "description")
      onSubmit({ title: `${iface.name}: tavsif`, submit: (g) => api.put(`${path}/description`, { description: desc || null, ...g }) });
    if (kind === "speed")
      onSubmit({
        title: `${iface.name}: tezlik/duplex`,
        submit: (g) => api.put(`${path}/speed`, { auto, speed: auto ? null : num(speed), duplex: auto ? null : duplex || null, ...g }),
      });
    if (kind === "trunk")
      onSubmit({ title: `${iface.name}: trunk VLANlar`, submit: (g) => api.post(`${path}/trunk-vlans`, { add: add || null, remove: remove || null, ...g }) });
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Sozlash: ${iface.name}`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button onClick={go}>Preview (dry-run)</Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Amal">
          <Select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="vlan">Port rejimi va VLAN</option>
            <option value="trunk">Trunk ruxsat etilgan VLANlarni o&apos;zgartirish</option>
            <option value="description">Tavsif</option>
            <option value="speed">Tezlik / duplex</option>
          </Select>
        </Field>
        {kind === "vlan" && (
          <>
            <Field label="Rejim">
              <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option>access</option>
                <option>trunk</option>
                {hybrid && <option>hybrid</option>}
              </Select>
            </Field>
            {mode === "access" ? (
              <Field label="Access VLAN">
                <Input type="number" value={accessVlan} onChange={(e) => setAccessVlan(e.target.value)} />
              </Field>
            ) : (
              <Field label="PVID">
                <Input type="number" value={pvid} onChange={(e) => setPvid(e.target.value)} />
              </Field>
            )}
            {mode === "trunk" && (
              <Field label="Ruxsat etilgan VLANlar" hint="Masalan: 10 20 100 to 110">
                <Input value={trunkVlans} onChange={(e) => setTrunkVlans(e.target.value)} />
              </Field>
            )}
            {mode === "hybrid" && (
              <>
                <Field label="Tagged VLANlar">
                  <Input value={tagged} onChange={(e) => setTagged(e.target.value)} />
                </Field>
                <Field label="Untagged VLANlar">
                  <Input value={untagged} onChange={(e) => setUntagged(e.target.value)} />
                </Field>
              </>
            )}
          </>
        )}
        {kind === "trunk" && (
          <>
            <Field label="Qo'shish" hint="Masalan: 30 40 to 45">
              <Input value={add} onChange={(e) => setAdd(e.target.value)} />
            </Field>
            <Field label="Olib tashlash">
              <Input value={remove} onChange={(e) => setRemove(e.target.value)} />
            </Field>
          </>
        )}
        {kind === "description" && (
          <Field label="Tavsif" hint="Bo'sh qoldirilsa tavsif o'chiriladi">
            <Input value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={242} />
          </Field>
        )}
        {kind === "speed" && (
          <>
            <Field label="Avto-negotiation">
              <Select value={auto ? "1" : "0"} onChange={(e) => setAuto(e.target.value === "1")}>
                <option value="1">Yoqilgan</option>
                <option value="0">O&apos;chirilgan (qo&apos;lda)</option>
              </Select>
            </Field>
            {!auto && (
              <>
                <Field label="Tezlik (Mbps)">
                  <Select value={speed} onChange={(e) => setSpeed(e.target.value)}>
                    <option value="">—</option>
                    {[10, 100, 1000, 10000].map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </Select>
                </Field>
                <Field label="Duplex">
                  <Select value={duplex} onChange={(e) => setDuplex(e.target.value)}>
                    <option value="">—</option>
                    <option>full</option>
                    <option>half</option>
                  </Select>
                </Field>
              </>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

function InterfaceDetail({ deviceId, name, onClose }: { deviceId: string; name: string; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["interface-detail", deviceId, name],
    queryFn: () => api.get<Record<string, unknown>>(`/devices/${deviceId}/interfaces/${ifPath(name)}`),
  });
  return (
    <Modal open onClose={onClose} title={`display interface ${name}`} wide>
      {q.isPending ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : <Code>{JSON.stringify(q.data, null, 2)}</Code>}
    </Modal>
  );
}
