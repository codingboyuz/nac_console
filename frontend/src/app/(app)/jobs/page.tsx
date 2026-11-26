"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Clock, ListChecks, Loader, Plus, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { JobForm } from "@/components/job-form";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, Pagination, Select, StatStrip, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCounts } from "@/hooks/use-counts";
import { Can, useCan } from "@/hooks/use-me";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Job, JobType, Page } from "@/lib/types";

const LIMIT = 50;

export default function JobsPage() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const types = useQuery({ queryKey: ["job-types"], queryFn: () => api.get<JobType[]>("/jobs/types") });
  const { data, error, isPending } = useQuery({
    queryKey: ["jobs", { status, type, offset }],
    queryFn: () => api.get<Page<Job>>("/jobs", { status, type, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });

  const can = useCan();
  const counts = useCounts("/jobs", "status", ["RUNNING", "PENDING", "SUCCESS", "PARTIAL", "FAILED"], { refetchInterval: 10_000 });
  const pick = (v: string) => () => {
    setStatus((cur) => (cur === v ? "" : v));
    setOffset(0);
  };

  return (
    <>
      <PageHeader
        title="Vazifalar"
        subtitle="Ko'p qurilmali bulk operatsiyalar (Celery navbati)"
        actions={
          <Can perm={P.JOBS_CREATE}>
            <Button onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> Yangi vazifa
            </Button>
          </Can>
        }
      />
      <StatStrip
        items={[
          { label: "Jami", value: counts[""] ?? "—", icon: <ListChecks />, onClick: pick(""), active: !status },
          { label: "Bajarilmoqda", value: counts.RUNNING ?? "—", tone: counts.RUNNING ? "default" : "muted", icon: <Loader />, onClick: pick("RUNNING"), active: status === "RUNNING" },
          { label: "Navbatda", value: counts.PENDING ?? "—", tone: "muted", icon: <Clock />, onClick: pick("PENDING"), active: status === "PENDING" },
          { label: "Muvaffaqiyatli", value: counts.SUCCESS ?? "—", tone: "good", icon: <CheckCircle2 />, onClick: pick("SUCCESS"), active: status === "SUCCESS" },
          { label: "Qisman", value: counts.PARTIAL ?? "—", tone: counts.PARTIAL ? "warn" : "muted", icon: <CheckCircle2 />, onClick: pick("PARTIAL"), active: status === "PARTIAL" },
          { label: "Xato", value: counts.FAILED ?? "—", tone: counts.FAILED ? "bad" : "muted", icon: <XCircle />, onClick: pick("FAILED"), active: status === "FAILED" },
        ]}
      />
      <Card bodyClassName="p-0">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <Select className="w-40" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="">Barcha holatlar</option>
            {["PENDING", "RUNNING", "SUCCESS", "PARTIAL", "FAILED", "CANCELLED"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Select className="w-56" value={type} onChange={(e) => { setType(e.target.value); setOffset(0); }}>
            <option value="">Barcha turlar</option>
            {types.data?.map((t) => (
              <option key={t.name}>{t.name}</option>
            ))}
          </Select>
        </div>
        {error ? (
          <div className="p-4"><ErrorBox error={error} /></div>
        ) : isPending ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty
            title={status || type ? "Mos vazifa topilmadi" : "Hali vazifa yo'q"}
            icon={<ListChecks />}
            action={
              !status && !type && can(P.JOBS_CREATE) ? (
                <Button onClick={() => setCreating(true)}>
                  <Plus className="h-4 w-4" /> Vazifa yaratish
                </Button>
              ) : undefined
            }
          >
            Vazifalar bir nechta qurilmada bir vaqtda bajariladigan o&apos;zgarishlar: VLAN, backup, buyruqlar va boshqalar.
          </Empty>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Nomi</Th>
                  <Th>Tur</Th>
                  <Th>Holat</Th>
                  <Th>Progress</Th>
                  <Th>Qurilmalar</Th>
                  <Th>Yaratilgan</Th>
                  <Th>Tugagan</Th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((j) => (
                  <tr key={j.id} className="hover:bg-slate-50">
                    <Td>
                      <Link href={`/jobs/${j.id}`} className="text-blue-600 hover:underline">{j.name}</Link>
                      {j.dry_run && <Badge tone="amber" className="ml-2">dry-run</Badge>}
                    </Td>
                    <Td className="font-mono text-xs">{j.type}</Td>
                    <Td><StatusBadge value={j.status} /></Td>
                    <Td>
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 w-24 overflow-hidden rounded bg-slate-100">
                          <div className="h-full bg-blue-500" style={{ width: `${j.progress}%` }} />
                        </div>
                        <span className="text-xs tabular-nums text-slate-500">{j.progress}%</span>
                      </div>
                    </Td>
                    <Td className="tabular-nums">{j.target_devices.length}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtDate(j.created_at)}</Td>
                    <Td className="whitespace-nowrap text-xs">{fmtDate(j.completed_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
      {creating && <JobForm open onClose={() => setCreating(false)} />}
    </>
  );
}
