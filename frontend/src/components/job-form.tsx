"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { useAllDevices } from "@/hooks/use-devices";
import { api, errorMessage, newId } from "@/lib/api";
import type { JobAccepted, JobCreate, JobType } from "@/lib/types";
import { Alert, Badge, Button, Checkbox, Code, Field, Input, Modal, Select, Textarea } from "./ui";

/** Builds a starter params object from a JSON schema's properties. */
function skeleton(schema: Record<string, unknown>): Record<string, unknown> {
  const props = (schema.properties ?? {}) as Record<string, { default?: unknown; type?: string }>;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) {
    if ("default" in v) out[k] = v.default;
    else out[k] = v.type === "array" ? [] : v.type === "object" ? {} : v.type === "integer" || v.type === "number" ? 0 : v.type === "boolean" ? false : "";
  }
  return out;
}

export function JobForm({ open, onClose, preset }: { open: boolean; onClose: () => void; preset?: Partial<JobCreate> }) {
  const router = useRouter();
  const types = useQuery({ queryKey: ["job-types"], queryFn: () => api.get<JobType[]>("/jobs/types"), enabled: open });
  const devices = useAllDevices();
  const [type, setType] = useState(preset?.type ?? "");
  const [targets, setTargets] = useState<string[]>(preset?.target_devices ?? []);
  const [filter, setFilter] = useState("");
  const [params, setParams] = useState(preset?.params ? JSON.stringify(preset.params, null, 2) : "{}");
  const [name, setName] = useState(preset?.name ?? "");
  const [concurrency, setConcurrency] = useState(10);
  const [stopOnFailure, setStopOnFailure] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [paramError, setParamError] = useState<string | null>(null);

  const jt = types.data?.find((t) => t.name === type);
  const visible = useMemo(
    () => (devices.data?.items ?? []).filter((d) => !filter || d.hostname.toLowerCase().includes(filter.toLowerCase()) || d.mgmt_ip.includes(filter)),
    [devices.data, filter],
  );

  const mut = useMutation({
    mutationFn: (body: JobCreate) =>
      api.post<JobAccepted>("/jobs", body, { "Idempotency-Key": newId() }),
    onSuccess: (r) => {
      toast.success("Vazifa navbatga qo'yildi");
      onClose();
      router.push(`/jobs/${r.job_id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = () => {
    let p: Record<string, unknown>;
    try {
      p = JSON.parse(params || "{}");
      setParamError(null);
    } catch {
      setParamError("Parametrlar yaroqli JSON emas");
      return;
    }
    mut.mutate({
      type,
      target_devices: targets,
      params: p,
      name: name || null,
      concurrency,
      stop_on_failure: stopOnFailure ? Number(stopOnFailure) : null,
      dry_run: dryRun,
      confirm: false,
    });
  };

  const toggle = (id: string) => setTargets((t) => (t.includes(id) ? t.filter((x) => x !== id) : [...t, id]));

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Yangi bulk vazifa"
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button onClick={submit} loading={mut.isPending} disabled={!type || !targets.length} variant={jt?.changes_config && !dryRun ? "warning" : "primary"}>
            {dryRun ? "Dry-run ishga tushirish" : "Ishga tushirish"}
          </Button>
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <Field label="Vazifa turi">
            <Select
              value={type}
              onChange={(e) => {
                setType(e.target.value);
                const t = types.data?.find((x) => x.name === e.target.value);
                if (t) setParams(JSON.stringify(skeleton(t.params_schema), null, 2));
              }}
            >
              <option value="">— tanlang —</option>
              {types.data?.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                  {t.changes_config ? " (konfiguratsiyani o'zgartiradi)" : ""}
                </option>
              ))}
            </Select>
          </Field>
          {jt && (
            <div className="flex flex-wrap gap-2 text-xs">
              <Badge>queue: {jt.queue}</Badge>
              <Badge>ruxsat: {jt.permission}</Badge>
              {jt.changes_config && <Badge tone="amber">config o&apos;zgaradi</Badge>}
            </div>
          )}
          <Field label="Nomi (ixtiyoriy)">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Parametrlar (JSON)">
            <Textarea rows={8} value={params} onChange={(e) => setParams(e.target.value)} />
          </Field>
          {paramError && <Alert tone="danger">{paramError}</Alert>}
          {jt && Object.keys(jt.params_schema.properties ?? {}).length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">Parametrlar sxemasi</summary>
              <Code className="mt-2">{JSON.stringify(jt.params_schema, null, 2)}</Code>
            </details>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Field label="Parallel (concurrency)">
              <Input type="number" min={1} max={100} value={concurrency} onChange={(e) => setConcurrency(Number(e.target.value))} />
            </Field>
            <Field label="N ta xatodan keyin to'xtash">
              <Input type="number" min={1} value={stopOnFailure} onChange={(e) => setStopOnFailure(e.target.value)} placeholder="cheklovsiz" />
            </Field>
          </div>
          <Checkbox label="Dry-run (avval preview; xavfli o'zgarishlar uchun tavsiya etiladi)" checked={dryRun} onChange={setDryRun} />
          {jt?.changes_config && !dryRun && (
            <Alert tone="warn">Xavfli (DANGEROUS) o&apos;zgarishlar dry-run va tasdiqlashsiz rad etiladi.</Alert>
          )}
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Maqsadli qurilmalar: {targets.length}</span>
            <span className="flex gap-2">
              <button className="text-blue-600 hover:underline" onClick={() => setTargets([...new Set([...targets, ...visible.map((d) => d.id)])])}>
                Hammasini tanlash
              </button>
              <button className="text-slate-500 hover:underline" onClick={() => setTargets([])}>
                Tozalash
              </button>
            </span>
          </div>
          <Input placeholder="Filtr..." value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="max-h-[45vh] overflow-y-auto rounded-md border border-slate-200">
            {visible.map((d) => (
              <label key={d.id} className="flex cursor-pointer items-center gap-2 border-b border-slate-200 px-3 py-1.5 text-sm hover:bg-slate-50">
                <input type="checkbox" className="accent-blue-600" checked={targets.includes(d.id)} onChange={() => toggle(d.id)} />
                <span className="flex-1 text-slate-800">{d.hostname}</span>
                <span className="font-mono text-xs text-slate-500">{d.mgmt_ip}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}
