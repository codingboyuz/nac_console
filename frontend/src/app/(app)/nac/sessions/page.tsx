"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownUp, RefreshCw, Unplug, Waypoints } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button, Card, Checkbox, Empty, ErrorBox, SearchInput, Loading, PageHeader, StatStrip, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCan } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtBytes, fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { CoaOut, NacSession } from "@/lib/types";

export default function SessionsPage() {
  const qc = useQueryClient();
  const can = useCan();
  const [active, setActive] = useState(true);
  const [mac, setMac] = useState("");
  const [macQuery, setMacQuery] = useState("");
  const q = useQuery({
    queryKey: ["sessions", { active, macQuery }],
    queryFn: () => api.get<NacSession[]>("/nac/sessions", { active, mac: macQuery }),
    refetchInterval: 15_000,
  });
  const coa = useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: "disconnect" | "reauthenticate" }) => api.post<CoaOut>(`/nac/sessions/${id}/${kind}`),
    onSuccess: (r) => {
      toast[r.acknowledged ? "success" : "warning"](`${r.kind}: ${r.acknowledged ? "NAS tasdiqladi (ACK)" : "NAK / javob yo'q"} — ${r.detail}`);
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const rows = q.data ?? [];
  const vlans = new Set(rows.map((r) => r.vlan).filter((v) => v !== null && v !== undefined)).size;
  const nas = new Set(rows.map((r) => r.nas_ip)).size;
  const traffic = rows.reduce((sum, r) => sum + (r.input_octets ?? 0) + (r.output_octets ?? 0), 0);

  return (
    <>
      <PageHeader title="RADIUS sessiyalari" subtitle="Accounting ma'lumotlari; CoA (RFC 5176) orqali uzish va qayta autentifikatsiya" />
      <StatStrip
        items={[
          { label: active ? "Faol sessiyalar" : "Sessiyalar", value: q.data ? rows.length : "—", icon: <Waypoints /> },
          { label: "Switchlar (NAS)", value: q.data ? nas : "—", tone: "muted" },
          { label: "VLANlar", value: q.data ? vlans : "—", tone: "muted" },
          { label: "Jami trafik", value: q.data ? fmtBytes(traffic) : "—", tone: "muted", icon: <ArrowDownUp /> },
        ]}
      />
      <Card bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <form
            onSubmit={(e: FormEvent) => {
              e.preventDefault();
              setMacQuery(mac.trim());
            }}
            className="flex gap-2"
          >
            <SearchInput className="w-60 font-mono" placeholder="MAC manzil" value={mac} onChange={(e) => setMac(e.target.value)} />
            <Button type="submit" variant="secondary">Qidirish</Button>
          </form>
          <Checkbox label="Faqat faol" checked={active} onChange={setActive} />
        </div>
        {q.isPending ? (
          <Loading />
        ) : q.error ? (
          <div className="p-4"><ErrorBox error={q.error} /></div>
        ) : !q.data?.length ? (
          <Empty title={active ? "Faol sessiya yo'q" : "Sessiya topilmadi"} icon={<Waypoints />}>
            Sessiyalar switchlar RADIUS accounting xabarlarini yuborganda paydo bo&apos;ladi. Switchlarda NAC provisioning va FreeRADIUS sozlanganini tekshiring.
          </Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>MAC</Th>
                <Th>Foydalanuvchi</Th>
                <Th>NAS / port</Th>
                <Th>IP</Th>
                <Th>VLAN</Th>
                <Th>Holat</Th>
                <Th>Trafik ↓/↑</Th>
                <Th>Boshlangan</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr key={s.id}>
                  <Td className="font-mono text-xs">{s.mac}</Td>
                  <Td>{s.username ?? "—"}</Td>
                  <Td className="font-mono text-xs">
                    {s.nas_ip}
                    {s.nas_port_id && <div className="text-slate-500">{s.nas_port_id}</div>}
                  </Td>
                  <Td className="font-mono text-xs">{s.framed_ip ?? "—"}</Td>
                  <Td>{s.vlan ?? "—"}</Td>
                  <Td><StatusBadge value={s.status} /></Td>
                  <Td className="whitespace-nowrap text-xs tabular-nums">
                    {fmtBytes(s.input_octets)} / {fmtBytes(s.output_octets)}
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(s.started_at)}</Td>
                  <Td>
                    {can(P.NAC_WRITE) && !s.stopped_at && (
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="secondary" title="Qayta autentifikatsiya (CoA)" onClick={() => coa.mutate({ id: s.id, kind: "reauthenticate" })}>
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                        <Button size="sm" variant="danger" title="Uzish (Disconnect)" onClick={() => coa.mutate({ id: s.id, kind: "disconnect" })}>
                          <Unplug className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
