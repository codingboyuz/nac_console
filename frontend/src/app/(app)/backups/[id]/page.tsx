"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, BadgeCheck, Download, GitCompare, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { GuardedChange } from "@/components/guarded-change";
import { Badge, Button, Card, Code, DiffView, ErrorBox, KV, Loading, PageHeader, Select, StatusBadge } from "@/components/ui";
import { useDeviceNames } from "@/hooks/use-devices";
import { Can } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtBytes, fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Backup, BackupDetail, Diff, Page, RestoreResponse } from "@/lib/types";

export default function BackupPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const name = useDeviceNames();
  const [compareTo, setCompareTo] = useState("");
  const [restoring, setRestoring] = useState(false);

  const q = useQuery({ queryKey: ["backups", id], queryFn: () => api.get<BackupDetail>(`/backups/${id}`) });
  const b = q.data;
  const siblings = useQuery({
    queryKey: ["backups", { device_id: b?.device_id }],
    queryFn: () => api.get<Page<Backup>>("/backups", { device_id: b!.device_id, limit: 100 }),
    enabled: !!b,
  });
  const diff = useQuery({
    queryKey: ["backup-diff", id, compareTo],
    queryFn: () => api.get<Diff>(`/backups/${compareTo}/diff/${id}`),
    enabled: !!compareTo,
  });
  const knownGood = useMutation({
    mutationFn: () => api.post(`/backups/${id}/known-good`),
    onSuccess: () => {
      toast.success("Known-good sifatida belgilandi");
      qc.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isPending) return <Loading />;
  if (q.error || !b) return <ErrorBox error={q.error ?? "Topilmadi"} />;

  const download = () => {
    const blob = new Blob([b.content ?? ""], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${name(b.device_id)}_${b.created_at.slice(0, 19).replace(/[:T]/g, "-")}.cfg`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <>
      <Link href="/backups" className="mb-3 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900">
        <ArrowLeft className="h-3.5 w-3.5" /> Backuplar
      </Link>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {name(b.device_id)} — {fmtDate(b.created_at)}
            {b.known_good && <Badge tone="green">known-good</Badge>}
          </span>
        }
        actions={
          <>
            <Button variant="secondary" onClick={download} disabled={!b.content}>
              <Download className="h-4 w-4" /> Yuklab olish
            </Button>
            <Can perm={P.BACKUPS_CREATE}>
              {!b.known_good && b.status === "SUCCESS" && (
                <Button variant="secondary" onClick={() => knownGood.mutate()} loading={knownGood.isPending}>
                  <BadgeCheck className="h-4 w-4" /> Known-good
                </Button>
              )}
            </Can>
            <Can perm={P.BACKUPS_RESTORE}>
              <Button variant="warning" onClick={() => setRestoring(true)} disabled={!b.content}>
                <RotateCcw className="h-4 w-4" /> Tiklash
              </Button>
            </Can>
          </>
        }
      />
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <Card
            title="Taqqoslash"
            actions={
              <Select className="w-72" value={compareTo} onChange={(e) => setCompareTo(e.target.value)}>
                <option value="">— boshqa backupni tanlang —</option>
                {siblings.data?.items
                  .filter((s) => s.id !== b.id)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {fmtDate(s.created_at)} ({s.trigger}){s.known_good ? " ✓" : ""}
                    </option>
                  ))}
              </Select>
            }
          >
            {!compareTo ? (
              <p className="flex items-center gap-2 text-sm text-slate-500">
                <GitCompare className="h-4 w-4" /> Unified diff ko&apos;rish uchun backup tanlang.
              </p>
            ) : diff.isPending ? (
              <Loading />
            ) : diff.error ? (
              <ErrorBox error={diff.error} />
            ) : (
              <DiffView diff={diff.data!.diff} />
            )}
          </Card>
          <Card title="Konfiguratsiya (maxfiy qiymatlar yashirilgan)">
            {b.content ? <Code className="max-h-[70vh]">{b.content}</Code> : <p className="text-sm text-slate-500">Kontent yo&apos;q</p>}
          </Card>
        </div>
        <Card title="Ma'lumotlar">
          <KV
            items={[
              ["Qurilma", <Link key="d" href={`/devices/${b.device_id}`} className="text-blue-600 hover:underline">{name(b.device_id)}</Link>],
              ["Holat", <StatusBadge key="s" value={b.status} />],
              ["Trigger", b.trigger],
              ["Manba", b.source],
              ["Hajm", fmtBytes(b.size)],
              ["Oila / VRP", `${b.family ?? "—"} / ${b.vrp_version ?? "—"}`],
              ["O'zgarish operatsiyasi", b.change_operation],
              ["SHA-256", <span key="h" className="break-all font-mono text-[10px]">{b.sha256}</span>],
            ]}
          />
          {b.error && <p className="mt-3 text-xs text-red-600">{b.error}</p>}
        </Card>
      </div>
      {restoring && (
        <GuardedChange<RestoreResponse>
          open
          title="Konfiguratsiyani tiklash"
          submit={(g) => api.post<RestoreResponse>(`/backups/${id}/restore`, g)}
          onClose={() => setRestoring(false)}
          onApplied={() => qc.invalidateQueries({ queryKey: ["backups"] })}
        />
      )}
    </>
  );
}
