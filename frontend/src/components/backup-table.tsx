"use client";

import { Archive } from "lucide-react";
import Link from "next/link";
import { useDeviceNames } from "@/hooks/use-devices";
import { fmtBytes, fmtDate } from "@/lib/format";
import type { Backup } from "@/lib/types";
import { Badge, Empty, StatusBadge, Table, Td, Th } from "./ui";

export function BackupTable({ items, hideDevice }: { items: Backup[]; hideDevice?: boolean }) {
  const name = useDeviceNames();
  if (!items.length) return <Empty title="Backup yo'q" icon={<Archive />}>Konfiguratsiya backuplari qurilma sahifasidan yoki jadval bo&apos;yicha avtomatik olinadi.</Empty>;
  return (
    <Table>
      <thead>
        <tr>
          <Th>Vaqt</Th>
          {!hideDevice && <Th>Qurilma</Th>}
          <Th>Trigger</Th>
          <Th>Manba</Th>
          <Th>Holat</Th>
          <Th>Hajm</Th>
          <Th>SHA-256</Th>
          <Th>VRP</Th>
          <Th />
        </tr>
      </thead>
      <tbody>
        {items.map((b) => (
          <tr key={b.id} className="hover:bg-slate-50">
            <Td className="whitespace-nowrap text-xs">
              <Link href={`/backups/${b.id}`} className="text-blue-600 hover:underline">
                {fmtDate(b.created_at)}
              </Link>
            </Td>
            {!hideDevice && (
              <Td>
                <Link href={`/devices/${b.device_id}`} className="hover:underline">
                  {name(b.device_id)}
                </Link>
              </Td>
            )}
            <Td>
              <Badge>{b.trigger}</Badge>
            </Td>
            <Td className="text-xs">{b.source}</Td>
            <Td>
              <StatusBadge value={b.status} />
              {b.error && <div className="text-xs text-red-600">{b.error}</div>}
            </Td>
            <Td className="text-xs tabular-nums">{fmtBytes(b.size)}</Td>
            <Td className="font-mono text-xs text-slate-500">{b.sha256?.slice(0, 12) ?? "—"}</Td>
            <Td className="text-xs">{b.vrp_version ?? "—"}</Td>
            <Td>{b.known_good && <Badge tone="green">known-good</Badge>}</Td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
