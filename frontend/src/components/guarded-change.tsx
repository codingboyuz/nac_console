"use client";

import { ShieldAlert } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { useMe } from "@/hooks/use-me";
import { ApiError, errorMessage } from "@/lib/api";
import type { Guard } from "@/lib/types";
import { Alert, Badge, Button, Checkbox, Code, DiffView, Field, Input, Modal, Spinner, StatusBadge } from "./ui";

export type ChangeLike = {
  status: string;
  category: string;
  reason: string;
  lines?: string[];
  lockout_findings?: { line: string; reason: string }[];
  confirmation_token?: string | null;
  diff?: string;
  notes?: string[];
  operation?: string;
};

type Props<R extends ChangeLike> = {
  open: boolean;
  onClose: () => void;
  title: string;
  /** Sends the request with the guard fields merged in. */
  submit: (guard: Guard) => Promise<R>;
  onApplied?: (result: R) => void;
  /** Show the "save" (write running config to startup) option. */
  allowSave?: boolean;
};

/**
 * Two-step guarded change: dry-run preview first, then apply. DANGEROUS changes need the
 * single-use confirmation token from the preview plus an audit reason. Lockout findings
 * (the change could cut the management path) can only be overridden by ADMIN with an
 * automatic rollback timer.
 */
export function GuardedChange<R extends ChangeLike>({ open, onClose, title, submit, onApplied, allowSave = true }: Props<R>) {
  const me = useMe();
  const instance = useId();
  const [nonce, setNonce] = useState(0);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [reason, setReason] = useState("");
  const [save, setSave] = useState(true);
  const [override, setOverride] = useState(false);
  const [rollback, setRollback] = useState(5);

  const guardExtras = (): Guard => ({
    save: allowSave ? save : undefined,
    override_lockout: override || undefined,
    rollback_minutes: override ? rollback : undefined,
  });

  // The dry-run is a query keyed per instance; bumping the nonce asks for a fresh preview
  // (and a fresh single-use confirmation token) with the current options.
  const previewQuery = useQuery({
    queryKey: ["guarded-preview", instance, nonce],
    queryFn: () => submit({ dry_run: true, ...guardExtras() }),
    enabled: open,
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
  });
  const preview = previewQuery.data ?? null;
  const loading = previewQuery.isFetching;
  const pErr = previewQuery.error;
  const lockout =
    pErr instanceof ApiError && pErr.problem.code === "lockout_risk" && Array.isArray(pErr.problem.details)
      ? (pErr.problem.details as { line: string; reason: string }[])
      : null;
  const error = applyError ?? (pErr ? errorMessage(pErr) : null);
  const runPreview = () => {
    setApplyError(null);
    setNonce((n) => n + 1);
  };

  const dangerous = preview?.category === "DANGEROUS";
  const noop = preview?.status === "noop";

  const apply = async () => {
    setApplying(true);
    setApplyError(null);
    try {
      const r = await submit({
        dry_run: false,
        confirm: dangerous ? true : undefined,
        confirmation_token: dangerous ? preview?.confirmation_token : undefined,
        reason: reason.trim() || undefined,
        ...guardExtras(),
      });
      toast.success(`O'zgarish qo'llandi: ${r.status}`);
      onApplied?.(r);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.problem.code === "confirmation_required") {
        // Token expired or already used: get a fresh preview.
        toast.error("Tasdiqlash tokeni yaroqsiz — preview qayta olinmoqda");
        runPreview();
      } else {
        setApplyError(errorMessage(e));
      }
    } finally {
      setApplying(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button variant="secondary" onClick={runPreview} loading={loading}>
            Previewni yangilash
          </Button>
          <Button
            variant={dangerous ? "danger" : "primary"}
            onClick={apply}
            loading={applying}
            disabled={!preview || noop || (dangerous && !reason.trim())}
          >
            {dangerous ? "Tasdiqlash va qo'llash" : "Qo'llash"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {loading && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner /> Dry-run bajarilmoqda...
          </div>
        )}
        {error && <Alert tone="danger">{error}</Alert>}

        {lockout && lockout.length > 0 && (
          <Alert tone="danger">
            <div className="mb-1 flex items-center gap-1.5 font-medium">
              <ShieldAlert className="h-4 w-4" /> Boshqaruv aloqasi uzilishi xavfi
            </div>
            <ul className="list-disc pl-5 text-xs">
              {lockout.map((f, i) => (
                <li key={i}>
                  <code>{f.line}</code> — {f.reason}
                </li>
              ))}
            </ul>
          </Alert>
        )}

        {preview && (
          <>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-slate-500">Holat:</span> <StatusBadge value={preview.status} />
              <span className="ml-3 text-slate-500">Toifa:</span> <StatusBadge value={preview.category} />
              {preview.operation && <Badge tone="slate">{preview.operation}</Badge>}
            </div>
            <p className="text-sm text-slate-500">{preview.reason}</p>
            {noop && <Alert tone="success">Kerakli holat allaqachon mavjud — hech narsa o&apos;zgarmaydi.</Alert>}
            {preview.lockout_findings && preview.lockout_findings.length > 0 && (
              <Alert tone="warn">
                Lockout ogohlantirishlari (override faol):
                <ul className="list-disc pl-5 text-xs">
                  {preview.lockout_findings.map((f, i) => (
                    <li key={i}>
                      <code>{f.line}</code> — {f.reason}
                    </li>
                  ))}
                </ul>
              </Alert>
            )}
            {preview.notes && preview.notes.length > 0 && (
              <Alert tone="info">
                <ul className="list-disc pl-5 text-xs">
                  {preview.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </Alert>
            )}
            {preview.diff !== undefined && preview.diff !== "" && (
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">Diff</div>
                <DiffView diff={preview.diff} />
              </div>
            )}
            {preview.lines && preview.lines.length > 0 && (
              <div>
                <div className="mb-1 text-xs font-medium text-slate-500">Qurilmaga yuboriladigan buyruqlar</div>
                <Code>{preview.lines.join("\n")}</Code>
              </div>
            )}
          </>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={dangerous ? "Audit sababi (majburiy)" : "Audit sababi"} hint="Masalan: CHG-1234" className="sm:col-span-2">
            <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="O'zgarish sababi" />
          </Field>
          {allowSave && <Checkbox label="Konfiguratsiyani saqlash (save/commit)" checked={save} onChange={setSave} />}
          {me.role === "ADMIN" && (lockout || override) && (
            <div className="space-y-2 sm:col-span-2">
              <Checkbox label="Lockout himoyasini chetlab o'tish (faqat ADMIN)" checked={override} onChange={setOverride} />
              {override && (
                <Field label="Avtomatik rollback (daqiqa, 1–30)">
                  <Input type="number" min={1} max={30} value={rollback} onChange={(e) => setRollback(Number(e.target.value))} />
                </Field>
              )}
              {override && (
                <Button size="sm" variant="warning" onClick={runPreview}>
                  Override bilan dry-run
                </Button>
              )}
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
