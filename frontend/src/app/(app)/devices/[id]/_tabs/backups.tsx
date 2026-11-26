"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Archive, GitCompare } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { BackupTable } from "@/components/backup-table";
import { Badge, Button, Card, DiffView, ErrorBox, Loading } from "@/components/ui";
import { Can } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { P } from "@/lib/perms";
import type { Backup, Device, Drift, JobAccepted, Page } from "@/lib/types";

export function BackupsTab({ device }: { device: Device }) {
  const router = useRouter();
  const [showDrift, setShowDrift] = useState(false);
  const list = useQuery({
    queryKey: ["backups", { device_id: device.id }],
    queryFn: () => api.get<Page<Backup>>("/backups", { device_id: device.id, limit: 100 }),
  });
  const drift = useQuery({
    queryKey: ["drift", device.id],
    queryFn: () => api.get<Drift>(`/devices/${device.id}/drift`),
    enabled: showDrift,
  });
  const backup = useMutation({
    mutationFn: () => api.post<JobAccepted>(`/devices/${device.id}/backup`),
    onSuccess: (r) => {
      toast.success("Backup vazifasi yaratildi");
      router.push(`/jobs/${r.job_id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <div className="space-y-6">
      <Card
        title="Backuplar"
        bodyClassName="p-0"
        actions={
          <>
            <Can perm={P.DEVICE_SHOW}>
              <Button size="sm" variant="secondary" onClick={() => setShowDrift(true)} loading={drift.isFetching}>
                <GitCompare className="h-3.5 w-3.5" /> Driftni tekshirish
              </Button>
            </Can>
            <Can perm={P.BACKUPS_CREATE}>
              <Button size="sm" onClick={() => backup.mutate()} loading={backup.isPending}>
                <Archive className="h-3.5 w-3.5" /> Hozir backup olish
              </Button>
            </Can>
          </>
        }
      >
        {list.isPending ? <Loading /> : list.error ? <ErrorBox error={list.error} /> : <BackupTable items={list.data!.items} hideDevice />}
      </Card>
      {showDrift && (
        <Card
          title={
            <span className="flex items-center gap-2">
              Drift: joriy konfiguratsiya vs known-good
              {drift.data && <Badge tone={drift.data.drifted ? "amber" : "green"}>{drift.data.drifted ? "farq bor" : "mos"}</Badge>}
            </span>
          }
          actions={drift.data?.baseline_id && <Link className="text-xs text-blue-600 hover:underline" href={`/backups/${drift.data.baseline_id}`}>Baseline</Link>}
        >
          {drift.isPending ? <Loading label="Qurilmadan konfiguratsiya o'qilmoqda..." /> : drift.error ? <ErrorBox error={drift.error} /> : <DiffView diff={drift.data!.diff} />}
        </Card>
      )}
    </div>
  );
}
