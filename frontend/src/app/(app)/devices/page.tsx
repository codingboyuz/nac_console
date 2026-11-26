"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Plus, Server, ServerCrash, ShieldAlert, Wrench } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { DeviceForm } from "@/components/device-form";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, Pagination, SearchInput, Select, StatStrip, StatusBadge, Table, Td, Th } from "@/components/ui";
import { useCounts } from "@/hooks/use-counts";
import { Can, useCan } from "@/hooks/use-me";
import { usePlatforms, vendorTone } from "@/hooks/use-platforms";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Device, Page } from "@/lib/types";

const LIMIT = 50;

export default function DevicesPage() {
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [family, setFamily] = useState("");
  const [platform, setPlatform] = useState("");
  const platforms = usePlatforms();
  const [sort, setSort] = useState("hostname");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);

  const { data, error, isPending } = useQuery({
    queryKey: ["devices", { q, status, family, platform, sort, offset }],
    queryFn: () =>
      api.get<Page<Device>>("/devices", { q, status, family, platform, sort, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  });

  const can = useCan();
  const counts = useCounts("/devices", "status", ["ONLINE", "UNREACHABLE", "OFFLINE", "AUTH_FAILED", "MAINTENANCE"]);
  const pick = (value: string) => () => {
    setStatus((cur) => (cur === value ? "" : value));
    setOffset(0);
  };
  const filtered = Boolean(q || status || family || platform);

  const reset = <T,>(fn: (v: T) => void) => (v: T) => {
    fn(v);
    setOffset(0);
  };

  return (
    <>
      <PageHeader
        title="Qurilmalar"
        subtitle="Huawei, Cisco va TP-Link kommutator va routerlar inventari"
        actions={
          <Can perm={P.DEVICES_WRITE}>
            <Button onClick={() => setCreating(true)}>
              <Plus className="h-4 w-4" /> Qurilma qo&apos;shish
            </Button>
          </Can>
        }
      />
      <StatStrip
        items={[
          { label: "Jami", value: counts[""] ?? "—", icon: <Server />, onClick: pick(""), active: !status },
          { label: "Onlayn", value: counts.ONLINE ?? "—", tone: "good", icon: <CheckCircle2 />, onClick: pick("ONLINE"), active: status === "ONLINE" },
          { label: "Yetib bo'lmaydi", value: counts.UNREACHABLE ?? "—", tone: counts.UNREACHABLE ? "bad" : "muted", icon: <ServerCrash />, onClick: pick("UNREACHABLE"), active: status === "UNREACHABLE" },
          { label: "Oflayn", value: counts.OFFLINE ?? "—", tone: counts.OFFLINE ? "bad" : "muted", icon: <AlertTriangle />, onClick: pick("OFFLINE"), active: status === "OFFLINE" },
          { label: "Autentifikatsiya xatosi", value: counts.AUTH_FAILED ?? "—", tone: counts.AUTH_FAILED ? "warn" : "muted", icon: <ShieldAlert />, onClick: pick("AUTH_FAILED"), active: status === "AUTH_FAILED" },
          { label: "Texnik xizmatda", value: counts.MAINTENANCE ?? "—", tone: "muted", icon: <Wrench />, onClick: pick("MAINTENANCE"), active: status === "MAINTENANCE" },
        ]}
      />
      <Card bodyClassName="p-0">
        <div className="flex flex-wrap gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <SearchInput className="max-w-64" placeholder="Hostname yoki IP bo'yicha qidirish..." value={q} onChange={(e) => reset(setQ)(e.target.value)} />
          <Select className="w-40" value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
            <option value="">Barcha holatlar</option>
            {["ONLINE", "OFFLINE", "UNREACHABLE", "AUTH_FAILED", "MAINTENANCE", "UNKNOWN"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Select className="w-56" value={platform} onChange={(e) => reset(setPlatform)(e.target.value)}>
            <option value="">Barcha platformalar</option>
            {platforms.data?.filter((p) => p.available).map((p) => (
              <option key={p.platform} value={p.platform}>
                {p.label}
              </option>
            ))}
          </Select>
          <Select className="w-36" value={family} onChange={(e) => reset(setFamily)(e.target.value)}>
            <option value="">Barcha oilalar</option>
            {["S", "CE", "AR", "UNKNOWN"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Select className="ml-auto w-44" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="hostname">Hostname ↑</option>
            <option value="-hostname">Hostname ↓</option>
            <option value="mgmt_ip">IP ↑</option>
            <option value="status">Holat</option>
            <option value="-last_seen">Oxirgi ko&apos;rilgan</option>
            <option value="-created_at">Yangi qo&apos;shilgan</option>
          </Select>
        </div>
        {error ? (
          <div className="p-4">
            <ErrorBox error={error} />
          </div>
        ) : isPending ? (
          <Loading />
        ) : !data?.items.length ? (
          filtered ? (
            <Empty title="Mos qurilma topilmadi" icon={<Server />}>
              Qidiruv yoki filtrlarni o&apos;zgartirib ko&apos;ring.
            </Empty>
          ) : (
            <Empty
              title="Inventar bo'sh"
              icon={<Server />}
              action={
                can(P.DEVICES_WRITE) && (
                  <Button onClick={() => setCreating(true)}>
                    <Plus className="h-4 w-4" /> Birinchi qurilmani qo&apos;shish
                  </Button>
                )
              }
            >
              Huawei, Cisco yoki TP-Link kommutatorini qo&apos;shing — platforma uni SSH, SNMP yoki API orqali boshqaradi.
            </Empty>
          )
        ) : (
          <>
            <Table>
              <thead>
                <tr>
                  <Th>Hostname</Th>
                  <Th>IP</Th>
                  <Th>Holat</Th>
                  <Th>Vendor / Model</Th>
                  <Th>OS versiya</Th>
                  <Th>Protokol</Th>
                  <Th>Sayt</Th>
                  <Th>Teglar</Th>
                  <Th>Oxirgi ko&apos;rilgan</Th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id}>
                    <Td>
                      <Link href={`/devices/${d.id}`} className="group flex items-center gap-2.5">
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md border border-slate-200 bg-slate-50 text-slate-500 group-hover:border-blue-200 group-hover:text-blue-600">
                          <Server className="h-4 w-4" />
                        </span>
                        <span>
                          <span className="block font-medium text-slate-900 group-hover:text-blue-600">{d.hostname}</span>
                          {d.description && <span className="block max-w-56 truncate text-xs text-slate-500">{d.description}</span>}
                        </span>
                      </Link>
                    </Td>
                    <Td className="font-mono text-xs">{d.mgmt_ip}</Td>
                    <Td>
                      <StatusBadge value={d.status} />
                    </Td>
                    <Td>
                      <Badge tone={vendorTone[d.vendor] ?? "slate"}>{d.vendor}</Badge>{" "}
                      {d.family !== "UNKNOWN" && <Badge tone="violet">{d.family}</Badge>}{" "}
                      <span className="text-xs text-slate-500">{d.model ?? ""}</span>
                    </Td>
                    <Td className="text-xs">{d.vrp_version ?? "—"}</Td>
                    <Td>
                      <span className="inline-flex items-center gap-1">
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] uppercase text-slate-600">{d.protocol}</span>
                        {d.security_warning && <AlertTriangle className="h-3.5 w-3.5 text-amber-600" />}
                      </span>
                    </Td>
                    <Td className="text-xs">{d.site ?? "—"}</Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {d.tags.map((t) => (
                          <Badge key={t}>{t}</Badge>
                        ))}
                      </div>
                    </Td>
                    <Td className="whitespace-nowrap text-xs text-slate-500">{fmtRelative(d.last_seen)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </Card>
      {creating && <DeviceForm open onClose={() => setCreating(false)} />}
    </>
  );
}
