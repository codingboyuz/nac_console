"use client";

import { useQuery } from "@tanstack/react-query";
import { BellRing, ChevronRight, LogOut, Menu, Network, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useState, type FormEvent, type ReactNode } from "react";
import { MeProvider, useMeQuery } from "@/hooks/use-me";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import { isActive, NAV, navFor } from "@/lib/nav";
import { P } from "@/lib/perms";
import type { Me, Page } from "@/lib/types";
import { Button, ErrorBox, Loading } from "./ui";

const ROLE_LABEL: Record<string, string> = {
  ADMIN: "Administrator",
  NETWORK_ADMIN: "Tarmoq administratori",
  OPERATOR: "Operator",
  AUDITOR: "Auditor",
  READ_ONLY: "Faqat o'qish",
};

async function logout() {
  await fetch("/api/auth/logout", { method: "POST" });
  window.location.assign("/login"); // full reload drops cached client state
}

function Brand() {
  return (
    <Link href="/" className="flex items-center gap-2.5 px-4">
      <div className="grid h-8 w-8 place-items-center rounded-lg bg-blue-600 text-white shadow-sm">
        <Network className="h-4.5 w-4.5" />
      </div>
      <div className="leading-tight">
        <div className="text-sm font-semibold text-slate-900">NAC Console</div>
        <div className="text-[11px] text-slate-500">Network Operations</div>
      </div>
    </Link>
  );
}

function Sidebar({ me, pathname, onNavigate }: { me: Me; pathname: string; onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
      {NAV.map((group) => {
        const items = group.items.filter((i) => !i.perm || me.permissions.includes(i.perm));
        if (!items.length) return null;
        return (
          <div key={group.section}>
            <div className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400">{group.section}</div>
            <div className="space-y-0.5">
              {items.map((item) => {
                const active = isActive(pathname, item.href);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onNavigate}
                    className={cn(
                      "group flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm font-medium transition-colors",
                      active ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
                    )}
                  >
                    <item.icon className={cn("h-4 w-4 shrink-0", active ? "text-blue-600" : "text-slate-400 group-hover:text-slate-600")} />
                    {item.label}
                  </Link>
                );
              })}
            </div>
          </div>
        );
      })}
    </nav>
  );
}

function Breadcrumbs({ pathname }: { pathname: string }) {
  const current = navFor(pathname);
  const deeper = current && current.href !== "/" && pathname.length > current.href.length + 1;
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-sm">
      <span className="hidden text-slate-400 sm:inline">{current?.section ?? "Umumiy"}</span>
      <ChevronRight className="hidden h-3.5 w-3.5 text-slate-300 sm:inline" />
      {deeper ? (
        <>
          <Link href={current.href} className="text-slate-500 hover:text-slate-900">
            {current.label}
          </Link>
          <ChevronRight className="h-3.5 w-3.5 text-slate-300" />
          <span className="truncate font-medium text-slate-900">Tafsilotlar</span>
        </>
      ) : (
        <span className="truncate font-medium text-slate-900">{current?.label ?? "Boshqaruv paneli"}</span>
      )}
    </div>
  );
}

function GlobalSearch() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = q.trim();
    if (!value) return;
    // MAC-like input goes to the MAC/ARP locator, everything else to the device inventory.
    const mac = /^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$|^([0-9a-f]{4}\.){2}[0-9a-f]{4}$/i.test(value);
    router.push(mac ? `/mac?q=${encodeURIComponent(value)}` : `/devices?q=${encodeURIComponent(value)}`);
    setQ("");
  };
  return (
    <form onSubmit={submit} className="relative hidden w-full max-w-sm md:block">
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Qurilma, IP yoki MAC qidirish..."
        className="h-9 w-full rounded-md border-0 bg-slate-100 pl-8 pr-3 text-sm text-slate-900 placeholder:text-slate-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-600"
      />
    </form>
  );
}

function AlertBell({ me }: { me: Me }) {
  const allowed = me.permissions.includes(P.ALERTS_READ);
  const firing = useQuery({
    queryKey: ["shell", "firing"],
    queryFn: () => api.get<Page<unknown>>("/alerts", { state: "FIRING", limit: 1 }).then((p) => p.total),
    enabled: allowed,
    refetchInterval: 30_000,
  });
  if (!allowed) return null;
  const n = firing.data ?? 0;
  return (
    <Link
      href="/alerts"
      className="relative rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
      aria-label={`Ogohlantirishlar: ${n} ta ochiq`}
      title={`${n} ta ochiq ogohlantirish`}
    >
      <BellRing className="h-4.5 w-4.5" />
      {n > 0 && (
        <span className="absolute right-0.5 top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white">
          {n > 99 ? "99+" : n}
        </span>
      )}
    </Link>
  );
}

function UserMenu({ me }: { me: Me }) {
  const [open, setOpen] = useState(false);
  const name = me.full_name || me.username;
  const initials = name
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        className="flex items-center gap-2 rounded-md py-1 pl-1 pr-2 hover:bg-slate-100"
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-slate-800 text-[11px] font-semibold text-white">{initials}</span>
        <span className="hidden text-left leading-tight lg:block">
          <span className="block text-sm font-medium text-slate-900">{name}</span>
          <span className="block text-[11px] text-slate-500">{ROLE_LABEL[me.role] ?? me.role}</span>
        </span>
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-1 w-56 rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
          <div className="border-b border-slate-100 px-3 py-2">
            <div className="truncate text-sm font-medium text-slate-900">{name}</div>
            <div className="truncate text-xs text-slate-500">{me.username}</div>
          </div>
          <button
            onMouseDown={logout}
            className="mt-1 flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-red-600 hover:bg-red-50"
          >
            <LogOut className="h-4 w-4" /> Tizimdan chiqish
          </button>
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { data: me, error, isPending } = useMeQuery();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);


  if (isPending) return <Loading label="Sessiya tekshirilmoqda..." />;
  if (error || !me)
    return (
      <div className="mx-auto max-w-md p-10">
        <ErrorBox error={error ?? "Sessiya topilmadi"} />
        <Button className="mt-4" onClick={logout}>
          Qayta kirish
        </Button>
      </div>
    );

  return (
    <MeProvider me={me}>
      <div className="min-h-screen">
        {/* Desktop sidebar */}
        <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-slate-200 bg-white lg:flex">
          <div className="flex h-14 items-center border-b border-slate-200">
            <Brand />
          </div>
          <Sidebar me={me} pathname={pathname} />
          <div className="border-t border-slate-200 px-4 py-3 text-[11px] text-slate-400">
            Huawei · Cisco · TP-Link
          </div>
        </aside>

        {/* Mobile drawer */}
        {mobileOpen && (
          <div className="fixed inset-0 z-40 lg:hidden">
            <div className="absolute inset-0 bg-slate-900/40" onClick={() => setMobileOpen(false)} />
            <aside className="absolute inset-y-0 left-0 flex w-64 flex-col bg-white shadow-xl">
              <div className="flex h-14 items-center justify-between border-b border-slate-200 pr-2">
                <Brand />
                <button onClick={() => setMobileOpen(false)} className="rounded-md p-2 text-slate-500 hover:bg-slate-100" aria-label="Yopish">
                  <X className="h-4 w-4" />
                </button>
              </div>
              <Sidebar me={me} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
            </aside>
          </div>
        )}

        <div className="lg:pl-64">
          <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
            <button onClick={() => setMobileOpen(true)} className="rounded-md p-2 text-slate-500 hover:bg-slate-100 lg:hidden" aria-label="Menyu">
              <Menu className="h-5 w-5" />
            </button>
            <Breadcrumbs pathname={pathname} />
            <div className="ml-auto flex flex-1 items-center justify-end gap-2">
              <GlobalSearch />
              <AlertBell me={me} />
              <div className="mx-1 h-6 w-px bg-slate-200" />
              <UserMenu me={me} />
            </div>
          </header>
          <main className="mx-auto min-w-0 max-w-[1600px] p-4 sm:p-6">
            <Suspense fallback={<Loading />}>{children}</Suspense>
          </main>
        </div>
      </div>
    </MeProvider>
  );
}
