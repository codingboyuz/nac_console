"use client";

import { KeySquare } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Alert, Button, Card, Empty, ErrorBox, Loading, StatusBadge, Table, Td, Th } from "@/components/ui";
import { Can } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { Device, HostKey } from "@/lib/types";

export function HostKeysTab({ device }: { device: Device }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["host-keys", device.id], queryFn: () => api.get<HostKey[]>(`/devices/${device.id}/host-keys`) });
  const approve = useMutation({
    mutationFn: (keyId: string) => api.post(`/devices/${device.id}/host-keys/${keyId}/approve`),
    onSuccess: () => {
      toast.success("Kalit tasdiqlandi");
      qc.invalidateQueries({ queryKey: ["host-keys", device.id] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const hasPending = q.data?.some((k) => k.status === "PENDING");

  return (
    <div className="space-y-4">
      {hasPending && (
        <Alert tone="warn">
          Qurilma yangi SSH kalit taqdim etdi. Barmoq izini (fingerprint) qurilma konsolidagi qiymat bilan solishtirmasdan tasdiqlamang —
          bu MITM hujumi belgisi bo&apos;lishi mumkin.
        </Alert>
      )}
      <Card title="SSH host kalitlari (TOFU)" bodyClassName="p-0">
        {q.isPending ? (
          <Loading />
        ) : q.error ? (
          <div className="p-4">
            <ErrorBox error={q.error} />
          </div>
        ) : !q.data?.length ? (
          <Empty title="Kalit saqlanmagan" icon={<KeySquare />}>Birinchi SSH ulanishda qurilma kaliti avtomatik qadaladi (TOFU). Keyingi ulanishlarda kalit o&apos;zgarsa, ulanish bloklanadi.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Tur</Th>
                <Th>Fingerprint</Th>
                <Th>Holat</Th>
                <Th>Yaratilgan</Th>
                <Th>Tasdiqlangan</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((k) => (
                <tr key={k.id}>
                  <Td className="text-xs">{k.key_type}</Td>
                  <Td className="break-all font-mono text-xs">{k.fingerprint}</Td>
                  <Td>
                    <StatusBadge value={k.status} />
                  </Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(k.created_at)}</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(k.approved_at)}</Td>
                  <Td>
                    {k.status === "PENDING" && (
                      <Can perm={P.HOSTKEY_APPROVE}>
                        <Button size="sm" variant="warning" onClick={() => approve.mutate(k.id)} loading={approve.isPending}>
                          Tasdiqlash
                        </Button>
                      </Can>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
