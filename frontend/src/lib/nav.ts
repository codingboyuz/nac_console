import {
  Activity,
  Archive,
  BellRing,
  Fingerprint,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  Network,
  Radar,
  ScrollText,
  Search,
  Server,
  ShieldCheck,
  Users,
  Waypoints,
} from "lucide-react";
import { P, type Perm } from "@/lib/perms";

export type NavItem = { href: string; label: string; icon: typeof Server; perm?: Perm };

export const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Umumiy",
    items: [{ href: "/", label: "Boshqaruv paneli", icon: LayoutDashboard }],
  },
  {
    section: "Tarmoq",
    items: [
      { href: "/devices", label: "Qurilmalar", icon: Server, perm: P.DEVICES_READ },
      { href: "/monitoring", label: "Monitoring", icon: Activity, perm: P.MONITORING_READ },
      { href: "/alerts", label: "Ogohlantirishlar", icon: BellRing, perm: P.ALERTS_READ },
      { href: "/jobs", label: "Vazifalar", icon: ListChecks, perm: P.JOBS_READ },
      { href: "/backups", label: "Backuplar", icon: Archive, perm: P.BACKUPS_READ },
      { href: "/mac", label: "MAC / ARP qidiruv", icon: Search, perm: P.DEVICES_READ },
      { href: "/discovery", label: "Discovery", icon: Radar, perm: P.DISCOVERY_RUN },
    ],
  },
  {
    section: "Kirishni nazorat qilish (NAC)",
    items: [
      { href: "/nac/endpoints", label: "Endpointlar", icon: Fingerprint, perm: P.NAC_READ },
      { href: "/nac/sessions", label: "Sessiyalar", icon: Waypoints, perm: P.NAC_READ },
      { href: "/nac/policies", label: "Siyosatlar", icon: ShieldCheck, perm: P.NAC_READ },
      { href: "/nac/radius", label: "RADIUS serverlar", icon: Network, perm: P.RADIUS_READ },
    ],
  },
  {
    section: "Tizim",
    items: [
      { href: "/credentials", label: "Credentiallar", icon: KeyRound, perm: P.DEVICES_READ },
      { href: "/users", label: "Foydalanuvchilar", icon: Users, perm: P.USERS_READ },
      { href: "/audit", label: "Audit jurnali", icon: ScrollText, perm: P.AUDIT_READ },
    ],
  },
];

export const isActive = (pathname: string, href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

/** The most specific navigation entry for a path (with its section). */
export function navFor(pathname: string) {
  const all = NAV.flatMap((g) => g.items.map((i) => ({ ...i, section: g.section })));
  return [...all].sort((a, b) => b.href.length - a.href.length).find((i) => isActive(pathname, i.href));
}
