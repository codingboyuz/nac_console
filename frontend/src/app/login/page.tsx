import { Activity, Archive, Network, ShieldCheck } from "lucide-react";
import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Kirish" };

const FEATURES = [
  { icon: Network, title: "Ko'p vendorli boshqaruv", text: "Huawei, Cisco va TP-Link kommutatorlari bitta paneldan" },
  { icon: ShieldCheck, title: "Xavfsiz o'zgarishlar", text: "Buyruqlar tasnifi, tasdiqlash va lockout himoyasi" },
  { icon: Activity, title: "Monitoring va ogohlantirish", text: "CPU, xotira, interfeyslar va hodisalar real vaqtda" },
  { icon: Archive, title: "Backup va audit", text: "Konfiguratsiya tarixi, farqlar va to'liq audit jurnali" },
];

export default function LoginPage() {
  return (
    <main className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden border-r border-slate-200 bg-gradient-to-br from-blue-50 via-white to-slate-50 p-12 lg:flex lg:flex-col">
        <div className="flex items-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-blue-600 text-white shadow-sm">
            <Network className="h-5 w-5" />
          </div>
          <div className="leading-tight">
            <div className="font-semibold text-slate-900">NAC Console</div>
            <div className="text-xs text-slate-500">Network Operations</div>
          </div>
        </div>
        <div className="my-auto max-w-md">
          <h2 className="text-3xl font-semibold tracking-tight text-slate-900">Tarmoq infratuzilmasini boshqarish markazi</h2>
          <p className="mt-3 text-slate-600">
            Kommutatorlar, kirishni nazorat qilish (NAC), monitoring va konfiguratsiya o&apos;zgarishlari — bitta xavfsiz tizimda.
          </p>
          <ul className="mt-10 space-y-5">
            {FEATURES.map((f) => (
              <li key={f.title} className="flex gap-3">
                <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-blue-600 shadow-xs">
                  <f.icon className="h-4 w-4" />
                </div>
                <div>
                  <div className="text-sm font-medium text-slate-900">{f.title}</div>
                  <div className="text-sm text-slate-500">{f.text}</div>
                </div>
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-slate-400">Faqat vakolatli tarmoq administratorlari uchun. Barcha amallar audit jurnaliga yoziladi.</p>
      </section>
      <section className="flex items-center justify-center bg-white p-6">
        <Suspense>
          <LoginForm />
        </Suspense>
      </section>
    </main>
  );
}
