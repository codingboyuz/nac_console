"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { BackupTable } from "@/components/backup-table";
import { Card, ErrorBox, Loading, PageHeader, Pagination, Select } from "@/components/ui";
import { useAllDevices } from "@/hooks/use-devices";
import { api } from "@/lib/api";
import type { Backup, Page } from "@/lib/types";

const LIMIT = 50;

export default function BackupsPage() {
  const devices = useAllDevices();
  const [deviceId, setDeviceId] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isPending } = useQuery({
    queryKey: ["backups", { device_id: deviceId, offset }],
    queryFn: () => api.get<Page<Backup>>("/backups", { device_id: deviceId, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Backuplar" subtitle="Shifrlangan, deduplikatsiyalangan konfiguratsiya nusxalari" />
      <Card bodyClassName="p-0">
        <div className="flex gap-2 border-b border-slate-200 p-3">
          <Select className="w-64" value={deviceId} onChange={(e) => { setDeviceId(e.target.value); setOffset(0); }}>
            <option value="">Barcha qurilmalar</option>
            {devices.data?.items.map((d) => (
              <option key={d.id} value={d.id}>{d.hostname}</option>
            ))}
          </Select>
        </div>
        {error ? (
          <div className="p-4"><ErrorBox error={error} /></div>
        ) : isPending ? (
          <Loading />
        ) : (
          <>
            <BackupTable items={data!.items} />
            <Pagination total={data!.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
    </>
  );
}
