"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Ban, Radio } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Fragment, useState } from "react";
import { toast } from "sonner";
import { Alert, Badge, Button, Card, Code, ErrorBox, Field, Input, KV, Loading, PageHeader, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useDeviceNames } from "@/hooks/use-devices";
import { useCan, useMe } from "@/hooks/use-me";
import { useBackendSocket } from "@/hooks/use-ws";
import { api, errorMessage, newId } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { JobAccepted, JobDetail } from "@/lib/types";

const FINAL = new Set(["SUCCESS", "PARTIAL", "FAILED", "CANCELLED"]);

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const router = useRouter();
  const can = useCan();
  const me = useMe();
  const name = useDeviceNames();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [reason, setReason] = useState("");

  const q = useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api.get<JobDetail>(`/jobs/${id}`),
    refetchInterval: (query) => (query.state.data && FINAL.has(query.state.data.status) ? false : 5000),
  });
  const job = q.data;
  const live = useBackendSocket(job && !FINAL.has(job.status) ? `/ws/jobs/${id}` : null, () => {
    qc.invalidateQueries({ queryKey: ["jobs", id] });
  });

  const cancel = useMutation({
    mutationFn: () => api.post(`/jobs/${id}/cancel`),
    onSuccess: () => {
      toast.success("Bekor qilish so'raldi");
      qc.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const confirmRun = useMutation({
    mutationFn: () =>
      api.post<JobAccepted>(
        "/jobs",
        {
          type: job!.type,
          name: job!.name,
          target_devices: job!.target_devices,
          params: job!.params,
          dry_run: false,
          confirm: true,
          confirmation_token: job!.confirmation_token,
          reason: reason.trim(),
        },
        { "Idempotency-Key": newId() },
      ),
    onSuccess: (r) => {
      toast.success("Tasdiqlangan vazifa ishga tushdi");
      router.push(`/jobs/${r.job_id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isPending) return <Loading />;
  if (q.error || !job) return <ErrorBox error={q.error ?? "Topilmadi"} />;

  const results = job.per_device_results ?? [];
  const counts = results.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {});
  const canCancel = !FINAL.has(job.status) && can(P.JOBS_CANCEL) && (job.created_by === me.id || me.role === "ADMIN" || me.role === "NETWORK_ADMIN");

  return (
    <>
      <Link href="/jobs" className="mb-3 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900">
        <ArrowLeft className="h-3.5 w-3.5" /> Vazifalar
      </Link>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {job.name} <StatusBadge value={job.status} />
            {job.dry_run && <Badge tone="amber">dry-run</Badge>}
            {live && (
              <span className="inline-flex items-center gap-1 text-xs font-normal text-emerald-600">
                <Radio className="h-3.5 w-3.5 animate-pulse" /> jonli
              </span>
            )}
          </span>
        }
        subtitle={<span className="font-mono">{job.type}</span>}
        actions={
          canCancel && (
            <Button variant="danger" onClick={() => cancel.mutate()} loading={cancel.isPending} disabled={job.cancel_requested}>
              <Ban className="h-4 w-4" /> {job.cancel_requested ? "Bekor qilinmoqda..." : "Bekor qilish"}
            </Button>
          )
        }
      />

      <div className="mb-6">
        <div className="mb-1 flex justify-between text-xs text-slate-500">
          <span>Progress</span>
          <span className="tabular-nums">{job.progress}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded bg-slate-100">
          <div className="h-full bg-blue-500 transition-all" style={{ width: `${job.progress}%` }} />
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          {Object.entries(counts).map(([s, n]) => (
            <span key={s} className="text-xs">
              <StatusBadge value={s} /> {n}
            </span>
          ))}
        </div>
      </div>

      {job.error && (
        <div className="mb-4">
          <Alert tone="danger">{job.error}</Alert>
        </div>
      )}

      {job.dry_run && job.confirmation_token && FINAL.has(job.status) && can(P.JOBS_CREATE) && (
        <Card title="Dry-run yakunlandi — tasdiqlash talab qilinadi" className="mb-6 border-amber-200">
          <div className="space-y-3">
            <p className="text-sm text-slate-500">
              Preview natijalarini quyida tekshiring. Bir xil parametrlar bilan haqiqiy o&apos;zgarishni ishga tushirish uchun audit sababini kiriting.
            </p>
            <Field label="Audit sababi (majburiy)">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="CHG-1234" />
            </Field>
            <Button variant="danger" disabled={!reason.trim()} loading={confirmRun.isPending} onClick={() => confirmRun.mutate()}>
              Tasdiqlash va ishga tushirish
            </Button>
          </div>
        </Card>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <Card title="Natijalar (qurilma bo'yicha)" className="xl:col-span-2" bodyClassName="p-0">
          <Table>
            <thead>
              <tr>
                <Th>Qurilma</Th>
                <Th>Holat</Th>
                <Th>Urinishlar</Th>
                <Th>Boshlangan</Th>
                <Th>Tugagan</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <Fragment key={r.device_id}>
                  <tr className="hover:bg-slate-50">
                    <Td>
                      <Link href={`/devices/${r.device_id}`} className="text-blue-600 hover:underline">{name(r.device_id)}</Link>
                      {r.error && <div className="text-xs text-red-600">{r.error}</div>}
                    </Td>
                    <Td><StatusBadge value={r.status} /></Td>
                    <Td className="tabular-nums">{r.attempts}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtDate(r.started_at)}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtDate(r.completed_at)}</Td>
                    <Td>
                      {Object.keys(r.output ?? {}).length > 0 && (
                        <Button size="sm" variant="ghost" onClick={() => setExpanded(expanded === r.device_id ? null : r.device_id)}>
                          {expanded === r.device_id ? "Yopish" : "Natija"}
                        </Button>
                      )}
                    </Td>
                  </tr>
                  {expanded === r.device_id && (
                    <tr>
                      <Td colSpan={6}>
                        <Code>{JSON.stringify(r.output, null, 2)}</Code>
                      </Td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </Table>
        </Card>
        <div className="space-y-6">
          <Card title="Tafsilotlar">
            <KV
              items={[
                ["ID", <span key="id" className="font-mono text-xs">{job.id}</span>],
                ["Navbat", job.queue],
                ["Qurilmalar", job.target_devices.length],
                ["Tasdiqlangan", job.confirmed ? "ha" : "yo'q"],
                ["Qayta urinishlar", job.retry_count],
                ["Yaratilgan", fmtDate(job.created_at)],
                ["Boshlangan", fmtDate(job.started_at)],
                ["Tugagan", fmtDate(job.completed_at)],
              ]}
            />
          </Card>
          <Card title="Parametrlar">
            <Code>{JSON.stringify(job.params, null, 2)}</Code>
          </Card>
          {Object.keys(job.output ?? {}).length > 0 && (
            <Card title="Umumiy natija">
              <Code>{JSON.stringify(job.output, null, 2)}</Code>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
