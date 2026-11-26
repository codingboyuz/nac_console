"use client";

import { Play } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input, StatusBadge } from "@/components/ui";
import { isHuawei, quickCommands } from "@/hooks/use-platforms";
import { ApiError, api, errorMessage } from "@/lib/api";
import { cn } from "@/lib/format";
import type { Device, ExecuteResponse } from "@/lib/types";

type Entry = { command: string; result?: ExecuteResponse; error?: string; at: string };


export function TerminalTab({ device }: { device: Device }) {
  const [command, setCommand] = useState("");
  const [history, setHistory] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<ExecuteResponse | null>(null);
  const [reason, setReason] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const push = (e: Entry) => {
    setHistory((h) => [...h.slice(-49), e]);
    setTimeout(() => bottom.current?.scrollIntoView({ behavior: "smooth" }), 50);
  };

  const run = async (cmd: string, extra: Record<string, unknown> = {}) => {
    setBusy(true);
    try {
      const r = await api.post<ExecuteResponse>(`/devices/${device.id}/execute-command`, { command: cmd, ...extra });
      if (r.dry_run && r.confirmation_token) {
        // DANGEROUS: backend returned a single-use token; ask for an audit reason.
        setConfirm(r);
        setReason("");
      } else {
        push({ command: cmd, result: r, at: new Date().toLocaleTimeString() });
      }
    } catch (e) {
      if (e instanceof ApiError && e.problem.code === "confirmation_required" && !extra.dry_run && !extra.confirm) {
        // DANGEROUS command: ask the backend for a dry-run to obtain the confirmation token.
        setBusy(false);
        return run(cmd, { dry_run: true });
      }
      push({ command: cmd, error: errorMessage(e), at: new Date().toLocaleTimeString() });
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const cmd = command.trim();
    if (!cmd) return;
    setCommand("");
    run(cmd);
  };

  return (
    <div className="space-y-4">
      <Alert tone="info">
        Har bir buyruq backend tomonidan tasniflanadi (SAFE / WARNING / DANGEROUS) va auditga yoziladi. Xavfli buyruqlar
        tasdiqlash tokeni va sabab talab qiladi. Ruxsat etilmagan buyruqlar rad etiladi.
      </Alert>
      <Card title={`Terminal — ${device.hostname}`} bodyClassName="p-0">
        <div className="h-[55vh] overflow-y-auto bg-[#0d1117] p-4 font-mono text-xs leading-relaxed text-slate-300">
          {history.length === 0 && <div className="text-slate-500">Buyruq kiriting yoki pastdagi tezkor buyruqlardan tanlang.</div>}
          {history.map((h, i) => (
            <div key={i} className="mb-3">
              <div className="flex items-center gap-2 text-sky-400">
                <span className="text-slate-500">[{h.at}]</span>
                <span>&lt;{device.hostname}&gt; {h.command}</span>
                {h.result && <StatusBadge value={h.result.category} />}
                {h.result?.elapsed_ms != null && <span className="text-slate-500">{h.result.elapsed_ms} ms</span>}
              </div>
              {h.error && <div className="whitespace-pre-wrap text-red-400">{h.error}</div>}
              {h.result?.security_warning && <div className="text-amber-400">{h.result.security_warning}</div>}
              {h.result && <pre className={cn("mt-1 whitespace-pre-wrap text-slate-200")}>{h.result.output ?? h.result.reason}</pre>}
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <form onSubmit={submit} className="flex gap-2 border-t border-slate-200 p-3">
          <Input className="font-mono" value={command} onChange={(e) => setCommand(e.target.value)} placeholder={isHuawei(device.platform) ? "display ..." : "show ..."} disabled={busy} autoFocus />
          <Button type="submit" loading={busy}>
            <Play className="h-3.5 w-3.5" /> Bajarish
          </Button>
        </form>
        <div className="flex flex-wrap gap-1.5 border-t border-slate-200 p-3">
          {quickCommands(device.platform).map((c) => (
            <Button key={c} size="sm" variant="secondary" className="font-mono" disabled={busy} onClick={() => run(c)}>
              {c}
            </Button>
          ))}
        </div>
      </Card>

      {confirm && (
        <Card title="Xavfli buyruqni tasdiqlash" className="border-red-200">
          <div className="space-y-3">
            <div className="font-mono text-sm text-red-700">{confirm.command}</div>
            <p className="text-sm text-slate-500">{confirm.reason}</p>
            {confirm.confirmation_expires_in && <p className="text-xs text-slate-500">Token {confirm.confirmation_expires_in} soniya amal qiladi.</p>}
            <Field label="Audit sababi (majburiy)">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="CHG-1234" />
            </Field>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setConfirm(null)}>
                Bekor qilish
              </Button>
              <Button
                variant="danger"
                disabled={!reason.trim()}
                loading={busy}
                onClick={() => {
                  const c = confirm;
                  setConfirm(null);
                  run(c.command, { confirm: true, confirmation_token: c.confirmation_token, reason: reason.trim() });
                }}
              >
                Tasdiqlash va bajarish
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
