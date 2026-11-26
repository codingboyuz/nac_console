import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";
import { Loading } from "@/components/ui";

// The shell reads the pathname and the session on the client, so it streams in after the static frame.
export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <Suspense fallback={<Loading label="Yuklanmoqda..." />}>
      <AppShell>{children}</AppShell>
    </Suspense>
  );
}
