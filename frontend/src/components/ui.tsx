"use client";

import { Inbox, Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  forwardRef,
  useEffect,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { cn } from "@/lib/format";
import { navFor } from "@/lib/nav";

/* ---------- Button ---------- */
type Variant = "primary" | "secondary" | "danger" | "ghost" | "warning";
const variants: Record<Variant, string> = {
  primary: "bg-blue-600 text-white shadow-sm hover:bg-blue-700 focus-visible:outline-blue-600",
  secondary: "bg-white text-slate-700 shadow-sm ring-1 ring-inset ring-slate-300 hover:bg-slate-50",
  danger: "bg-red-600 text-white shadow-sm hover:bg-red-700 focus-visible:outline-red-600",
  warning: "bg-amber-500 text-white shadow-sm hover:bg-amber-600 focus-visible:outline-amber-500",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900",
};

export const Button = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }
>(function Button({ variant = "primary", size = "md", loading, className, children, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        variants[variant],
        className,
      )}
      disabled={disabled || loading}
      {...rest}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
});

/* ---------- Form controls ---------- */
const control =
  "w-full rounded-md border-0 bg-white px-3 text-sm text-slate-900 shadow-sm ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-600 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return <input ref={ref} className={cn(control, "h-9", className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <select ref={ref} className={cn(control, "h-9 pr-8", className)} {...rest}>
      {children}
    </select>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(
  function Textarea({ className, ...rest }, ref) {
    return <textarea ref={ref} className={cn(control, "py-2 font-mono", className)} {...rest} />;
  },
);

export function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1.5", className)}>
      <span className="block text-xs font-medium text-slate-700">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Checkbox({ label, checked, onChange, disabled }: { label: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700">
      <input
        type="checkbox"
        className="h-4 w-4 rounded border-slate-300 accent-blue-600"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

/* ---------- Layout ---------- */
export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cn("min-w-0 rounded-lg border border-slate-200 bg-white shadow-xs", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-slate-900">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          <div className="flex shrink-0 items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={cn("p-4", bodyClassName)}>{children}</div>
    </section>
  );
}

export function CardLink({ href, children = "Barchasi" }: { href: string; children?: ReactNode }) {
  return (
    <Link href={href} className="text-xs font-medium text-blue-600 hover:text-blue-700">
      {children} →
    </Link>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
  icon,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  /** Defaults to the navigation icon of the current section; pass null to hide it. */
  icon?: ReactNode | null;
}) {
  const pathname = usePathname();
  const nav = navFor(pathname);
  const NavIcon = nav?.icon;
  const glyph = icon === undefined ? (NavIcon ? <NavIcon /> : null) : icon;
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3.5">
        {glyph && (
          <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-blue-600 shadow-xs [&>svg]:h-5 [&>svg]:w-5">
            {glyph}
          </div>
        )}
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight text-slate-900">{title}</h1>
          {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** Filter / action row at the top of a list card. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap items-center gap-2 border-b border-slate-200 bg-slate-50/60 px-3 py-2.5", className)}>{children}</div>;
}

export const SearchInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function SearchInput(
  { className, ...rest },
  ref,
) {
  return (
    <div className={cn("relative w-full max-w-xs", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      <input ref={ref} className={cn(control, "h-9 pl-8")} {...rest} />
    </div>
  );
});

/** Compact row of headline numbers above a list. */
export function StatStrip({
  items,
}: {
  items: { label: string; value: ReactNode; tone?: "default" | "good" | "bad" | "warn" | "muted"; icon?: ReactNode; onClick?: () => void; active?: boolean }[];
}) {
  const tones = { default: "text-slate-900", good: "text-emerald-600", bad: "text-red-600", warn: "text-amber-600", muted: "text-slate-400" };
  return (
    <div className="mb-5 grid grid-cols-2 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xs sm:grid-cols-3 lg:flex lg:divide-x lg:divide-slate-200">
      {items.map((it) => {
        const body = (
          <>
            <div className="flex items-center gap-1.5 text-xs font-medium text-slate-500 [&>svg]:h-3.5 [&>svg]:w-3.5">
              {it.icon}
              {it.label}
            </div>
            <div className={cn("mt-1 text-xl font-semibold tabular-nums", tones[it.tone ?? "default"])}>{it.value}</div>
          </>
        );
        const cls = cn("flex-1 border-b border-slate-100 px-4 py-3 text-left lg:border-b-0", it.active && "bg-blue-50/60 shadow-[inset_0_-2px_0_0_var(--color-blue-600)]");
        return it.onClick ? (
          <button key={it.label} onClick={it.onClick} className={cn(cls, "transition-colors hover:bg-slate-50")}>
            {body}
          </button>
        ) : (
          <div key={it.label} className={cls}>
            {body}
          </div>
        );
      })}
    </div>
  );
}

/** Titled group of fields inside a form. */
export function FormSection({ title, description, children, className }: { title: string; description?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <fieldset className={cn("border-t border-slate-200 pt-4 first:border-t-0 first:pt-0", className)}>
      <legend className="sr-only">{title}</legend>
      <div className="mb-3">
        <div className="text-sm font-semibold text-slate-900">{title}</div>
        {description && <div className="mt-0.5 text-xs text-slate-500">{description}</div>}
      </div>
      {children}
    </fieldset>
  );
}

type StatTone = "default" | "good" | "bad" | "warn";
const statAccent: Record<StatTone, string> = {
  default: "bg-blue-50 text-blue-600",
  good: "bg-emerald-50 text-emerald-600",
  bad: "bg-red-50 text-red-600",
  warn: "bg-amber-50 text-amber-600",
};

export function Stat({
  label,
  value,
  tone = "default",
  icon,
  hint,
  href,
}: {
  label: string;
  value: ReactNode;
  tone?: StatTone;
  icon?: ReactNode;
  hint?: ReactNode;
  href?: string;
}) {
  const body = (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="text-xs font-medium text-slate-500">{label}</div>
        <div className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">{value}</div>
        {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
      </div>
      {icon && <div className={cn("rounded-md p-2 [&>svg]:h-4 [&>svg]:w-4", statAccent[tone])}>{icon}</div>}
    </div>
  );
  const cls = "block rounded-lg border border-slate-200 bg-white p-4 shadow-xs";
  return href ? (
    <Link href={href} className={cn(cls, "transition-colors hover:border-slate-300 hover:bg-slate-50/60")}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/* ---------- Feedback ---------- */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-5 w-5 animate-spin text-slate-400", className)} />;
}

export function Loading({ label = "Yuklanmoqda..." }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
      <Spinner /> {label}
    </div>
  );
}

export function Empty({
  children = "Ma'lumot yo'q",
  title,
  icon,
  action,
}: {
  children?: ReactNode;
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-12 text-center">
      <div className="mb-3 grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-400 [&>svg]:h-5 [&>svg]:w-5">{icon ?? <Inbox />}</div>
      {title && <div className="text-sm font-semibold text-slate-900">{title}</div>}
      <div className={cn("max-w-md text-sm text-slate-500", title && "mt-1")}>{children}</div>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{msg}</div>;
}

export function Alert({ tone = "info", children }: { tone?: "info" | "warn" | "danger" | "success"; children: ReactNode }) {
  const tones = {
    info: "border-blue-200 bg-blue-50 text-blue-800",
    warn: "border-amber-200 bg-amber-50 text-amber-800",
    danger: "border-red-200 bg-red-50 text-red-800",
    success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  };
  return <div className={cn("rounded-md border px-3 py-2 text-sm", tones[tone])}>{children}</div>;
}

/* ---------- Badge ---------- */
type Tone = "green" | "red" | "amber" | "sky" | "slate" | "violet";
const badgeTones: Record<Tone, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  red: "bg-red-50 text-red-700 ring-red-600/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/25",
  sky: "bg-blue-50 text-blue-700 ring-blue-600/20",
  slate: "bg-slate-50 text-slate-600 ring-slate-500/20",
  violet: "bg-violet-50 text-violet-700 ring-violet-600/20",
};
const dotTones: Record<Tone, string> = {
  green: "bg-emerald-500",
  red: "bg-red-500",
  amber: "bg-amber-500",
  sky: "bg-blue-500",
  slate: "bg-slate-400",
  violet: "bg-violet-500",
};

export function Badge({ tone = "slate", children, className, dot }: { tone?: Tone; children: ReactNode; className?: string; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        badgeTones[tone],
        className,
      )}
    >
      {dot && <span className={cn("h-1.5 w-1.5 rounded-full", dotTones[tone])} />}
      {children}
    </span>
  );
}

const statusTone: Record<string, Tone> = {
  ONLINE: "green", SUCCESS: "green", AUTHORIZED: "green", ACTIVE: "green", TRUSTED: "green", RESOLVED: "green", OK: "green", ACCEPT: "green", SAFE: "green", ok: "green", success: "green", applied: "green",
  OFFLINE: "red", UNREACHABLE: "red", FAILED: "red", BLOCKED: "red", REJECTED: "red", CRITICAL: "red", critical: "red", DANGEROUS: "red", REJECT: "red", failed: "red", error: "red",
  AUTH_FAILED: "amber", PARTIAL: "amber", QUARANTINED: "amber", PENDING: "amber", WARNING: "amber", warning: "amber", major: "amber", ACKNOWLEDGED: "amber", acknowledged: "amber", dry_run: "amber",
  RUNNING: "sky", MAINTENANCE: "violet", CANCELLED: "slate", UNKNOWN: "slate", info: "sky", INFO: "sky", minor: "sky", open: "red", OPEN: "red", firing: "red", FIRING: "red",
};

export function statusToneOf(value?: string | null): Tone {
  if (!value) return "slate";
  return statusTone[value] ?? statusTone[value.toUpperCase()] ?? "slate";
}

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <Badge tone={statusToneOf(value)} dot>
      {value}
    </Badge>
  );
}

/* ---------- Table ---------- */
export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("overflow-x-auto", className)}>
      <table className="w-full text-left text-sm [&_tbody_tr:hover]:bg-slate-50/80 [&_tbody_tr:last-child>td]:border-b-0">{children}</table>
    </div>
  );
}
export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "whitespace-nowrap border-b border-slate-200 bg-slate-50/80 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500",
        className,
      )}
    >
      {children}
    </th>
  );
}
export function Td({ children, className, colSpan }: { children?: ReactNode; className?: string; colSpan?: number }) {
  return (
    <td colSpan={colSpan} className={cn("border-b border-slate-100 px-3 py-2.5 text-slate-700", className)}>
      {children}
    </td>
  );
}

export function Pagination({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return <div className="border-t border-slate-200 px-3 py-2 text-xs text-slate-500">Jami: {total}</div>;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.max(1, Math.ceil(total / limit));
  return (
    <div className="flex items-center justify-between border-t border-slate-200 px-3 py-2 text-xs text-slate-500">
      <span>
        {offset + 1}–{Math.min(offset + limit, total)} / {total} · {page}/{pages}-sahifa
      </span>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          Oldingi
        </Button>
        <Button size="sm" variant="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          Keyingi
        </Button>
      </div>
    </div>
  );
}

/* ---------- Modal ---------- */
export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/40 p-4 pt-[8vh] backdrop-blur-[1px]" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={cn("w-full rounded-xl border border-slate-200 bg-white shadow-2xl", wide ? "max-w-4xl" : "max-w-lg")}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3.5">
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <button onClick={onClose} className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Yopish">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[70vh] overflow-y-auto p-5">{children}</div>
        {footer && <div className="flex justify-end gap-2 rounded-b-xl border-t border-slate-200 bg-slate-50 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- Tabs ---------- */
export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="mb-5 flex gap-1 overflow-x-auto border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "-mb-px whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
            value === t.id ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

/* ---------- Code ---------- */
export function Code({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <pre className={cn("max-h-[60vh] overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-relaxed text-slate-800", className)}>
      {children}
    </pre>
  );
}

export function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <Empty>Farq yo&apos;q</Empty>;
  return (
    <pre className="max-h-[60vh] overflow-auto rounded-md border border-slate-200 bg-white p-3 font-mono text-xs leading-relaxed">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={cn(
            "-mx-3 px-3",
            line.startsWith("+") && !line.startsWith("+++") && "bg-emerald-50 text-emerald-800",
            line.startsWith("-") && !line.startsWith("---") && "bg-red-50 text-red-800",
            line.startsWith("@@") && "bg-blue-50/60 text-blue-700",
            !/^[+\-@]/.test(line) && "text-slate-600",
          )}
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-8 text-sm sm:grid-cols-2">
      {items.map(([k, v], i) => (
        <div key={i} className="flex justify-between gap-3 border-b border-slate-100 py-2">
          <dt className="text-slate-500">{k}</dt>
          <dd className="text-right font-medium text-slate-900">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ---------- Meter (utilisation bar with status thresholds) ---------- */
export function Meter({
  value,
  warn = 70,
  crit = 90,
  mode = "load",
}: {
  value: number | null | undefined;
  warn?: number;
  crit?: number;
  /** load: high is bad (CPU, memory); health: low is bad (availability). */
  mode?: "load" | "health";
}) {
  if (value === null || value === undefined) return <span className="text-slate-400">—</span>;
  const pct = Math.max(0, Math.min(100, value));
  const tone =
    mode === "health"
      ? pct < 90 ? "bg-red-500" : pct < 99 ? "bg-amber-500" : "bg-emerald-500"
      : pct >= crit ? "bg-red-500" : pct >= warn ? "bg-amber-500" : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right text-xs tabular-nums text-slate-600">{Math.round(pct)}%</span>
    </div>
  );
}
