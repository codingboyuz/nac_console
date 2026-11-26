"use client";

import { ScrollText } from "lucide-react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Pagination, Select, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useDeviceNames } from "@/hooks/use-devices";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Audit, Page } from "@/lib/types";

const LIMIT = 100;

export default function AuditPage() {
  const name = useDeviceNames();
  const [operation, setOperation] = useState("");
  const [opQuery, setOpQuery] = useState("");
  const [success, setSuccess] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isPending } = useQuery({
    queryKey: ["audit", { opQuery, success, offset }],
    queryFn: () => api.get<Page<Audit>>("/logs/audit", { operation: opQuery, success, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Audit jurnali" subtitle="O'zgartirib bo'lmaydigan (append-only) harakatlar jurnali" />
      <Card bodyClassName="p-0">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              setOpQuery(operation.trim());
              setOffset(0);
            }}
            className="flex gap-2"
          >
            <Input className="w-64" placeholder="Operatsiya (masalan: auth.login)" value={operation} onChange={(e) => setOperation(e.target.value)} />
            <Button type="submit" variant="secondary">Filtr</Button>
          </form>
          <Select className="w-40" value={success} onChange={(e) => { setSuccess(e.target.value); setOffset(0); }}>
            <option value="">Barchasi</option>
            <option value="true">Muvaffaqiyatli</option>
            <option value="false">Muvaffaqiyatsiz</option>
          </Select>
        </div>
        {error ? (
          <div className="p-4"><ErrorBox error={error} /></div>
        ) : isPending ? (
          <Loading />
        ) : !data?.items.length ? (
          <Empty title="Yozuv topilmadi" icon={<ScrollText />}>Tanlangan filtr bo&apos;yicha audit yozuvlari yo&apos;q.</Empty>
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Vaqt</Th>
                  <Th>Foydalanuvchi</Th>
                  <Th>Operatsiya</Th>
                  <Th>Natija</Th>
                  <Th>Qurilma</Th>
                  <Th>Toifa</Th>
                  <Th>Xulosa / sabab</Th>
                  <Th>IP</Th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id} className="align-top">
                    <Td className="whitespace-nowrap text-xs">{fmtDate(a.timestamp)}</Td>
                    <Td className="text-xs">
                      {a.username ?? "—"}
                      {a.role && <div className="text-slate-500">{a.role}</div>}
                    </Td>
                    <Td className="font-mono text-xs">{a.operation}</Td>
                    <Td>{a.success ? <Badge tone="green">ok</Badge> : <Badge tone="red">rad</Badge>}</Td>
                    <Td className="text-xs">{a.device_id ? name(a.device_id) : "—"}</Td>
                    <Td>{a.command_category ? <StatusBadge value={a.command_category} /> : "—"}</Td>
                    <Td className="max-w-md text-xs">
                      {a.result_summary}
                      {a.reason && <div className="text-amber-700">Sabab: {a.reason}</div>}
                    </Td>
                    <Td className="font-mono text-xs text-slate-500">{a.source_ip ?? "—"}</Td>
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
