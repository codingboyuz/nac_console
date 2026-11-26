"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Route as RouteIcon, Settings2 } from "lucide-react";
import { useState } from "react";
import { GuardedChange } from "@/components/guarded-change";
import { Alert, Badge, Button, Card, Code, Empty, ErrorBox, Field, Input, KV, Loading, Modal, Select, Table, Td, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { api } from "@/lib/api";
import { P } from "@/lib/perms";
import type { ChangeResponse, Device, Guard, Route, Stp } from "@/lib/types";

type Pending = { title: string; submit: (g: Guard) => Promise<ChangeResponse> } | null;

export function RoutingTab({ device }: { device: Device }) {
  const qc = useQueryClient();
  const can = useCan();
  const canConf = can(P.DEVICE_CONFIGURE);
  const [pending, setPending] = useState<Pending>(null);
  const [stpEdit, setStpEdit] = useState(false);
  const [routeEdit, setRouteEdit] = useState(false);

  const stp = useQuery({ queryKey: ["stp", device.id], queryFn: () => api.get<Stp>(`/devices/${device.id}/stp`) });
  const routes = useQuery({ queryKey: ["routes", device.id], queryFn: () => api.get<Route[]>(`/devices/${device.id}/routing/table`) });
  const ospf = useQuery({ queryKey: ["ospf", device.id], queryFn: () => api.get<unknown[]>(`/devices/${device.id}/routing/ospf/neighbors`) });
  const bgp = useQuery({ queryKey: ["bgp", device.id], queryFn: () => api.get<unknown[]>(`/devices/${device.id}/routing/bgp/peers`) });

  return (
    <div className="space-y-6">
      <Card
        title="Spanning Tree"
        actions={
          canConf && (
            <Button size="sm" variant="secondary" onClick={() => setStpEdit(true)}>
              <Settings2 className="h-3.5 w-3.5" /> Sozlash
            </Button>
          )
        }
      >
        {stp.isPending ? (
          <Loading />
        ) : stp.error ? (
          <ErrorBox error={stp.error} />
        ) : (
          <div className="space-y-4">
            {stp.data!.loop_suspected && <Alert tone="danger">Petlya (loop) ehtimoli aniqlandi: TC soni yuqori.</Alert>}
            <KV
              items={[
                ["Rejim", stp.data!.mode],
                ["Bridge ID", stp.data!.bridge_id],
                ["Root ID", stp.data!.root_id],
                ["Root bridge", stp.data!.is_root ? <Badge key="r" tone="sky">ha</Badge> : "yo'q"],
                ["Root port", stp.data!.root_port],
                ["Root path cost", stp.data!.root_path_cost],
                ["TC soni", stp.data!.tc_count],
                ["Oxirgi TC", stp.data!.time_since_last_tc],
                ["BPDU protection", stp.data!.bpdu_protection === null ? "—" : stp.data!.bpdu_protection ? "yoqilgan" : "o'chirilgan"],
              ]}
            />
            {stp.data!.ports.length > 0 && (
              <Table>
                <thead>
                  <tr>
                    <Th>Instance</Th>
                    <Th>Port</Th>
                    <Th>Rol</Th>
                    <Th>Holat</Th>
                    <Th>Himoya</Th>
                  </tr>
                </thead>
                <tbody>
                  {stp.data!.ports.map((p, i) => (
                    <tr key={i}>
                      <Td>{p.instance}</Td>
                      <Td className="font-mono text-xs">{p.interface}</Td>
                      <Td>{p.role}</Td>
                      <Td>{p.state}</Td>
                      <Td className="text-xs">{p.protection}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </div>
        )}
      </Card>

      <Card
        title="Marshrutlash jadvali"
        bodyClassName="p-0"
        actions={
          canConf && (
            <Button size="sm" variant="secondary" onClick={() => setRouteEdit(true)}>
              <Plus className="h-3.5 w-3.5" /> Statik marshrut
            </Button>
          )
        }
      >
        {routes.isPending ? (
          <Loading />
        ) : routes.error ? (
          <div className="p-4">
            <ErrorBox error={routes.error} />
          </div>
        ) : !routes.data?.length ? (
          <Empty title="Marshrutlar yo'q" icon={<RouteIcon />}>Marshrutlash jadvali bo&apos;sh yoki hali yuklanmagan.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Prefiks</Th>
                <Th>Protokol</Th>
                <Th>Pref</Th>
                <Th>Cost</Th>
                <Th>Flags</Th>
                <Th>Next hop</Th>
                <Th>Interfeys</Th>
              </tr>
            </thead>
            <tbody>
              {routes.data.map((r, i) => (
                <tr key={i}>
                  <Td className="font-mono text-xs">{r.prefix}</Td>
                  <Td>
                    <Badge>{r.protocol}</Badge>
                  </Td>
                  <Td>{r.preference}</Td>
                  <Td>{r.cost}</Td>
                  <Td className="text-xs">{r.flags}</Td>
                  <Td className="font-mono text-xs">{r.nexthop}</Td>
                  <Td className="font-mono text-xs">{r.interface ?? "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="OSPF qo'shnilar">
          {ospf.isPending ? <Loading /> : ospf.error ? <ErrorBox error={ospf.error} /> : ospf.data!.length ? <Code>{JSON.stringify(ospf.data, null, 2)}</Code> : <Empty />}
        </Card>
        <Card title="BGP peerlar">
          {bgp.isPending ? <Loading /> : bgp.error ? <ErrorBox error={bgp.error} /> : bgp.data!.length ? <Code>{JSON.stringify(bgp.data, null, 2)}</Code> : <Empty />}
        </Card>
      </div>

      {stpEdit && (
        <StpEditor
          onClose={() => setStpEdit(false)}
          onSubmit={(body) => {
            setStpEdit(false);
            setPending({ title: "STP sozlamalari", submit: (g) => api.put(`/devices/${device.id}/stp`, { ...body, ...g }) });
          }}
        />
      )}
      {routeEdit && (
        <StaticRouteEditor
          onClose={() => setRouteEdit(false)}
          onSubmit={(body) => {
            setRouteEdit(false);
            const { action, ...payload } = body;
            setPending({
              title: `Statik marshrut: ${action} ${body.prefix}`,
              submit: (g) => api.post(`/devices/${device.id}/routing/static`, { ...payload, ...g }),
            });
          }}
        />
      )}
      {pending && (
        <GuardedChange
          open
          title={pending.title}
          submit={pending.submit}
          onClose={() => setPending(null)}
          onApplied={() => {
            qc.invalidateQueries({ queryKey: ["stp", device.id] });
            qc.invalidateQueries({ queryKey: ["routes", device.id] });
          }}
        />
      )}
    </div>
  );
}

function StpEditor({ onClose, onSubmit }: { onClose: () => void; onSubmit: (b: Record<string, unknown>) => void }) {
  const [mode, setMode] = useState("");
  const [priority, setPriority] = useState("");
  const [instance, setInstance] = useState("0");
  return (
    <Modal
      open
      onClose={onClose}
      title="STP sozlamalari"
      footer={
        <Button onClick={() => onSubmit({ mode: mode || null, priority: priority ? Number(priority) : null, instance: Number(instance) })} disabled={!mode && !priority}>
          Preview (dry-run)
        </Button>
      }
    >
      <div className="space-y-3">
        <Field label="Rejim">
          <Select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="">o&apos;zgarmaydi</option>
            <option>stp</option>
            <option>rstp</option>
            <option>mstp</option>
          </Select>
        </Field>
        <Field label="Bridge priority" hint="0–61440, 4096 ga karrali">
          <Input type="number" step={4096} min={0} max={61440} value={priority} onChange={(e) => setPriority(e.target.value)} />
        </Field>
        <Field label="Instance">
          <Input type="number" min={0} value={instance} onChange={(e) => setInstance(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function StaticRouteEditor({ onClose, onSubmit }: { onClose: () => void; onSubmit: (b: { action: string; prefix: string } & Record<string, unknown>) => void }) {
  const [action, setAction] = useState("add");
  const [prefix, setPrefix] = useState("");
  const [nexthop, setNexthop] = useState("");
  const [iface, setIface] = useState("");
  const [preference, setPreference] = useState("");
  const [vpn, setVpn] = useState("");
  const [description, setDescription] = useState("");
  return (
    <Modal
      open
      onClose={onClose}
      title="Statik marshrut"
      footer={
        <Button
          disabled={!prefix}
          onClick={() =>
            onSubmit({
              action,
              prefix,
              remove: action === "remove",
              next_hop: nexthop || null,
              interface: iface || null,
              preference: preference ? Number(preference) : null,
              vpn_instance: vpn || null,
              description: description || null,
            })
          }
        >
          Preview (dry-run)
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Amal">
          <Select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="add">Qo&apos;shish</option>
            <option value="remove">O&apos;chirish</option>
          </Select>
        </Field>
        <Field label="Prefiks" hint="0.0.0.0/0 = default">
          <Input value={prefix} onChange={(e) => setPrefix(e.target.value)} placeholder="10.0.0.0/8" />
        </Field>
        <Field label="Next hop">
          <Input value={nexthop} onChange={(e) => setNexthop(e.target.value)} />
        </Field>
        <Field label="Chiqish interfeysi">
          <Input value={iface} onChange={(e) => setIface(e.target.value)} />
        </Field>
        <Field label="Preference">
          <Input type="number" value={preference} onChange={(e) => setPreference(e.target.value)} />
        </Field>
        <Field label="VPN instance">
          <Input value={vpn} onChange={(e) => setVpn(e.target.value)} />
        </Field>
        <Field label="Tavsif" className="sm:col-span-2">
          <Input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={35} />
        </Field>
      </div>
    </Modal>
  );
}
