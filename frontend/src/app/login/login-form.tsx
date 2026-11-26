"use client";

import { Network } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, Field, Input } from "@/components/ui";

function safeNext(v: string | null): string {
  // Only same-origin relative paths: blocks open redirects like //evil.test.
  if (!v || !v.startsWith("/") || v.startsWith("//") || v.startsWith("/\\")) return "/";
  return v;
}

export function LoginForm() {
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        const msgs: Record<number, string> = {
          401: "Login yoki parol noto'g'ri",
          423: "Hisob vaqtincha bloklangan (ko'p muvaffaqiyatsiz urinish)",
          429: `Juda ko'p urinish. ${res.headers.get("retry-after") ?? ""} soniyadan keyin qayta urining`,
          503: "Backend bilan aloqa yo'q",
        };
        throw new Error(msgs[res.status] ?? data.message ?? data.detail ?? `Xato: HTTP ${res.status}`);
      }
      window.location.assign(safeNext(params.get("next"))); // full reload drops cached client state
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setLoading(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="w-full max-w-sm space-y-5">
      <div className="mb-8">
        <div className="mb-6 grid h-10 w-10 place-items-center rounded-lg bg-blue-600 text-white shadow-sm lg:hidden">
          <Network className="h-5 w-5" />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Tizimga kirish</h1>
        <p className="mt-1.5 text-sm text-slate-500">Hisob ma&apos;lumotlaringizni kiriting</p>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      <Field label="Foydalanuvchi nomi">
        <Input autoFocus autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </Field>
      <Field label="Parol">
        <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </Field>
      <Button type="submit" className="h-10 w-full" loading={loading}>
        Kirish
      </Button>
      <p className="pt-2 text-center text-xs text-slate-400">Parolni unutdingizmi? Tizim administratoriga murojaat qiling.</p>
    </form>
  );
}
