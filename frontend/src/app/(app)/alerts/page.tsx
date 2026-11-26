"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, BellRing, Check, CheckCheck, CheckCircle2, ListChecks, Plus, Radio } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, Checkbox, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Pagination, Select, StatStrip, StatusBadge, Table, Tabs, Td, Th } from "@/components/ui";
import { useCounts } from "@/hooks/use-counts";
import { useDeviceNames } from "@/hooks/use-devices";
import { useCan } from "@/hooks/use-me";
import { useBackendSocket } from "@/hooks/use-ws";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Alert, AlertRule, Page } from "@/lib/types";

const LIMIT = 50;

export default function AlertsPage() {
  const [tab, setTab] = useState<"alerts" | "rules">("alerts");
  return (
    <>
      <PageHeader title="Ogohlantirishlar" subtitle="Monitoring qoidalari bo'yicha hosil bo'lgan hodisalar" />
      <Tabs
        tabs={[
          { id: "alerts", label: "Hodisalar" },
          { id: "rules", label: "Qoidalar" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "alerts" ? <AlertList /> : <RuleList />}
    </>
  );
}

function AlertList() {
  const qc = useQueryClient();
  const can = useCan();
  const name = useDeviceNames();
  const [state, setState] = useState("FIRING");
  const [severity, setSeverity] = useState("");
  const [offset, setOffset] = useState(0);

  const { data, error, isPending } = useQuery({
    queryKey: ["alerts", { state, severity, offset }],
    queryFn: () => api.get<Page<Alert>>("/alerts", { state, severity, limit: LIMIT, offset, sort: "-last_seen" }),
    placeholderData: keepPreviousData,
  });
  const live = useBackendSocket<{ type: string }>("/ws/alerts", (m) => {
    if (m.type === "alert") qc.invalidateQueries({ queryKey: ["alerts"] });
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "ack" | "resolve" }) => api.post(`/alerts/${id}/${action}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  const byState = useCounts("/alerts", "state", ["FIRING", "ACKNOWLEDGED", "RESOLVED"]);
  const firing = useCounts("/alerts", "severity", ["critical", "warning"], { base: { state: "FIRING" } });
  const pickState = (v: string) => () => {
    setState(v);
    setSeverity("");
    setOffset(0);
  };

  return (
    <>
    <StatStrip
      items={[
        { label: "Ochiq", value: byState.FIRING ?? "—", tone: byState.FIRING ? "bad" : "good", icon: <BellRing />, onClick: pickState("FIRING"), active: state === "FIRING" && !severity },
        { label: "Kritik (ochiq)", value: firing.critical ?? "—", tone: firing.critical ? "bad" : "muted", icon: <AlertOctagon />, onClick: () => { setState("FIRING"); setSeverity("critical"); setOffset(0); }, active: severity === "critical" },
        { label: "Ogohlantirish (ochiq)", value: firing.warning ?? "—", tone: firing.warning ? "warn" : "muted", icon: <AlertTriangle />, onClick: () => { setState("FIRING"); setSeverity("warning"); setOffset(0); }, active: severity === "warning" },
        { label: "Tasdiqlangan", value: byState.ACKNOWLEDGED ?? "—", icon: <Check />, onClick: pickState("ACKNOWLEDGED"), active: state === "ACKNOWLEDGED" },
        { label: "Hal qilingan", value: byState.RESOLVED ?? "—", tone: "good", icon: <CheckCircle2 />, onClick: pickState("RESOLVED"), active: state === "RESOLVED" },
      ]}
    />
    <Card bodyClassName="p-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
        <Select className="w-44" value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}>
          <option value="">Barcha holatlar</option>
          <option value="FIRING">FIRING</option>
          <option value="ACKNOWLEDGED">ACKNOWLEDGED</option>
          <option value="RESOLVED">RESOLVED</option>
        </Select>
        <Select className="w-40" value={severity} onChange={(e) => { setSeverity(e.target.value); setOffset(0); }}>
          <option value="">Barcha darajalar</option>
          {SEVERITIES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        {live && (
          <span className="ml-auto inline-flex items-center gap-1 text-xs text-emerald-600">
            <Radio className="h-3.5 w-3.5 animate-pulse" /> jonli
          </span>
        )}
      </div>
      {error ? (
        <div className="p-4"><ErrorBox error={error} /></div>
      ) : isPending ? (
        <Loading />
      ) : !data?.items.length ? (
        <Empty title={state === "FIRING" ? "Hammasi joyida" : "Hodisa topilmadi"} icon={state === "FIRING" ? <CheckCircle2 /> : <BellRing />}>
          {state === "FIRING" ? "Hozircha ochiq ogohlantirish yo'q. Yangi hodisalar shu yerda real vaqtda paydo bo'ladi." : "Tanlangan filtr bo'yicha hodisa yo'q."}
        </Empty>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <Th>Daraja</Th>
                <Th>Holat</Th>
                <Th>Tur</Th>
                <Th>Xabar</Th>
                <Th>Qurilma / port</Th>
                <Th>Soni</Th>
                <Th>Birinchi</Th>
                <Th>Oxirgi</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((a) => (
                <tr key={a.id} className="hover:bg-slate-50">
                  <Td><StatusBadge value={a.severity} /></Td>
                  <Td>
                    <StatusBadge value={a.state} />
                    {a.suppressed && <Badge className="ml-1">suppressed</Badge>}
                  </Td>
                  <Td className="font-mono text-xs">{a.alert_type}</Td>
                  <Td className="max-w-md">{a.message}</Td>
                  <Td className="text-xs">
                    {name(a.device_id)}
                    {a.interface && <div className="font-mono text-slate-500">{a.interface}</div>}
                  </Td>
                  <Td className="tabular-nums">{a.occurrences}</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(a.first_seen)}</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtRelative(a.last_seen)}</Td>
                  <Td>
                    {can(P.ALERTS_ACK) && a.state !== "RESOLVED" && (
                      <div className="flex justify-end gap-1">
                        {a.state === "FIRING" && (
                          <Button size="sm" variant="secondary" title="Qabul qilish" onClick={() => act.mutate({ id: a.id, action: "ack" })}>
                            <Check className="h-3.5 w-3.5" />
                          </Button>
                        )}
                        <Button size="sm" variant="secondary" title="Hal qilingan" onClick={() => act.mutate({ id: a.id, action: "resolve" })}>
                          <CheckCheck className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
          <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </Card>
    </>
  );
}

const ALERT_TYPES = ["high_cpu", "high_memory", "crc_errors", "high_traffic", "interface_down"];
const SEVERITIES = ["critical", "warning", "info"];

function RuleList() {
  const qc = useQueryClient();
  const can = useCan();
  const [editing, setEditing] = useState<Partial<AlertRule> | null>(null);
  const q = useQuery({ queryKey: ["alert-rules"], queryFn: () => api.get<AlertRule[]>("/alert-rules") });
  const save = useMutation({
    mutationFn: (r: Partial<AlertRule>) => {
      const { id: _id, ...body } = r;
      void _id;
      return api.put("/alert-rules", body);
    },
    onSuccess: () => {
      toast.success("Qoida saqlandi");
      setEditing(null);
      qc.invalidateQueries({ queryKey: ["alert-rules"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Card
      bodyClassName="p-0"
      title="Ogohlantirish qoidalari"
      actions={
        can(P.ALERTS_WRITE) && (
          <Button size="sm" onClick={() => setEditing({ alert_type: "high_cpu", operator: "gt", severity: "warning", enabled: true, for_seconds: 0, channels: [], scope: {} })}>
            <Plus className="h-3.5 w-3.5" /> Qoida
          </Button>
        )
      }
    >
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <div className="p-4"><ErrorBox error={q.error} /></div>
      ) : !q.data?.length ? (
        <Empty title="Qoida yo'q" icon={<ListChecks />}>Ogohlantirish qoidalari CPU, xotira, port holati kabi metrikalar chegaradan oshganda hodisa yaratadi.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Nomi</Th>
              <Th>Tur</Th>
              <Th>Shart</Th>
              <Th>Daraja</Th>
              <Th>Kanallar</Th>
              <Th>Holat</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {q.data.map((r) => (
              <tr key={r.id}>
                <Td className="font-medium">{r.name}</Td>
                <Td className="font-mono text-xs">{r.alert_type}</Td>
                <Td className="font-mono text-xs">
                  {r.metric ? `${r.metric} ${r.operator} ${r.threshold ?? ""}` : "—"}
                  {r.for_seconds ? ` (${r.for_seconds}s)` : ""}
                </Td>
                <Td><StatusBadge value={r.severity} /></Td>
                <Td className="text-xs">{r.channels?.join(", ") || "—"}</Td>
                <Td>{r.enabled ? <Badge tone="green">yoqilgan</Badge> : <Badge>o&apos;chirilgan</Badge>}</Td>
                <Td>
                  {can(P.ALERTS_WRITE) && (
                    <Button size="sm" variant="ghost" onClick={() => setEditing(r)}>
                      Tahrirlash
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {editing && <RuleEditor rule={editing} onClose={() => setEditing(null)} onSave={(r) => save.mutate(r)} saving={save.isPending} />}
    </Card>
  );
}

function RuleEditor({ rule, onClose, onSave, saving }: { rule: Partial<AlertRule>; onClose: () => void; onSave: (r: Partial<AlertRule>) => void; saving: boolean }) {
  const [r, setR] = useState(rule);
  const [channels, setChannels] = useState((rule.channels ?? []).join(", "));
  const set = <K extends keyof AlertRule>(k: K, v: AlertRule[K]) => setR((s) => ({ ...s, [k]: v }));
  const numOrNull = (v: string) => (v === "" ? null : Number(v));
  return (
    <Modal
      open
      onClose={onClose}
      title={rule.id ? `Qoida: ${rule.name}` : "Yangi qoida"}
      footer={
        <Button
          loading={saving}
          disabled={!r.name}
          onClick={() => onSave({ ...r, channels: channels.split(/[,\s]+/).filter(Boolean) })}
        >
          Saqlash
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Nomi (unikal, a-z 0-9 _ . -)" className="sm:col-span-2">
          <Input value={r.name ?? ""} onChange={(e) => set("name", e.target.value)} disabled={!!rule.id} />
        </Field>
        <Field label="Tur">
          <Select value={r.alert_type} onChange={(e) => set("alert_type", e.target.value)}>
            {[...new Set([...ALERT_TYPES, r.alert_type ?? ""])].filter(Boolean).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </Select>
        </Field>
        <Field label="Daraja">
          <Select value={r.severity} onChange={(e) => set("severity", e.target.value)}>
            {SEVERITIES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </Field>
        <Field label="Metrika" hint="cpu, memory, temperature, if_util_pct ...">
          <Input value={r.metric ?? ""} onChange={(e) => set("metric", e.target.value || null)} />
        </Field>
        <Field label="Operator">
          <Select value={r.operator} onChange={(e) => set("operator", e.target.value)}>
            {["gt", "lt"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </Field>
        <Field label="Chegara">
          <Input type="number" value={r.threshold ?? ""} onChange={(e) => set("threshold", numOrNull(e.target.value))} />
        </Field>
        <Field label="Tozalash chegarasi">
          <Input type="number" value={r.clear_threshold ?? ""} onChange={(e) => set("clear_threshold", numOrNull(e.target.value))} />
        </Field>
        <Field label="Davomiylik (s)">
          <Input type="number" value={r.for_seconds ?? 0} onChange={(e) => set("for_seconds", Number(e.target.value))} />
        </Field>
        <Field label="Kanallar" hint="Vergul bilan: webhook, telegram, email">
          <Input value={channels} onChange={(e) => setChannels(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Checkbox label="Yoqilgan" checked={r.enabled ?? true} onChange={(v) => set("enabled", v)} />
        </div>
      </div>
    </Modal>
  );
}
