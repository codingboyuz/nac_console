"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Pencil, Plus, UserX, Users } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, Checkbox, Empty, ErrorBox, Field, Input, Loading, Modal, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { useCan, useMe } from "@/hooks/use-me";
import { api, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { P } from "@/lib/perms";
import type { DeviceGroup, Page, Role, User } from "@/lib/types";

const ROLES: Role[] = ["ADMIN", "NETWORK_ADMIN", "OPERATOR", "AUDITOR", "READ_ONLY"];

export default function UsersPage() {
  const qc = useQueryClient();
  const can = useCan();
  const me = useMe();
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [pwd, setPwd] = useState<User | null>(null);
  const q = useQuery({ queryKey: ["users"], queryFn: () => api.get<Page<User>>("/users", { limit: 500 }) });
  const deactivate = useMutation({
    mutationFn: (id: string) => api.del(`/users/${id}`),
    onSuccess: () => {
      toast.success("Foydalanuvchi o'chirildi (deaktivatsiya)");
      qc.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const canWrite = can(P.USERS_WRITE);

  return (
    <>
      <PageHeader
        title="Foydalanuvchilar"
        subtitle="Rollar va qurilma guruhlari bo'yicha ruxsatlar"
        actions={
          <>
            <Button variant="secondary" onClick={() => setPwd(me as unknown as User)}>
              <KeyRound className="h-4 w-4" /> Parolimni o&apos;zgartirish
            </Button>
            {canWrite && (
              <Button onClick={() => setEditing("new")}>
                <Plus className="h-4 w-4" /> Foydalanuvchi
              </Button>
            )}
          </>
        }
      />
      <Card bodyClassName="p-0">
        {q.isPending ? (
          <Loading />
        ) : q.error ? (
          <div className="p-4"><ErrorBox error={q.error} /></div>
        ) : !q.data?.items.length ? (
          <Empty title="Foydalanuvchi yo'q" icon={<Users />}>Tizimga kiradigan administratorlar va operatorlarni qo&apos;shing.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Foydalanuvchi</Th>
                <Th>Ism</Th>
                <Th>Email</Th>
                <Th>Rol</Th>
                <Th>Guruh scope</Th>
                <Th>Holat</Th>
                <Th>Oxirgi kirish</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((u) => (
                <tr key={u.id}>
                  <Td className="font-medium">
                    {u.username} {u.is_service_account && <Badge tone="violet">service</Badge>}
                  </Td>
                  <Td>{u.full_name ?? "—"}</Td>
                  <Td className="text-xs">{u.email ?? "—"}</Td>
                  <Td><Badge tone={u.role === "ADMIN" ? "red" : u.role === "NETWORK_ADMIN" ? "amber" : "sky"}>{u.role}</Badge></Td>
                  <Td className="text-xs">{u.group_scope.length ? `${u.group_scope.length} guruh` : "hammasi"}</Td>
                  <Td>{u.is_active ? <Badge tone="green">faol</Badge> : <Badge>nofaol</Badge>}</Td>
                  <Td className="whitespace-nowrap text-xs">{fmtDate(u.last_login_at)}</Td>
                  <Td>
                    {canWrite && (
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setEditing(u)}>
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button size="sm" variant="ghost" title="Parolni o'rnatish" onClick={() => setPwd(u)}>
                          <KeyRound className="h-3.5 w-3.5" />
                        </Button>
                        {u.is_active && u.id !== me.id && (
                          <Button size="sm" variant="ghost" title="Deaktivatsiya" onClick={() => deactivate.mutate(u.id)}>
                            <UserX className="h-3.5 w-3.5 text-red-600" />
                          </Button>
                        )}
                      </div>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
      {editing && <UserForm user={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {pwd && <PasswordForm user={pwd} self={pwd.id === me.id} onClose={() => setPwd(null)} />}
    </>
  );
}

function UserForm({ user, onClose }: { user: User | null; onClose: () => void }) {
  const qc = useQueryClient();
  const groups = useQuery({ queryKey: ["device-groups"], queryFn: () => api.get<Page<DeviceGroup>>("/device-groups", { limit: 500 }) });
  const [username, setUsername] = useState(user?.username ?? "");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "READ_ONLY");
  const [scope, setScope] = useState<string[]>(user?.group_scope ?? []);
  const [active, setActive] = useState(user?.is_active ?? true);
  const mut = useMutation({
    mutationFn: () =>
      user
        ? api.patch(`/users/${user.id}`, { full_name: fullName || null, email: email || null, role, group_scope: scope, is_active: active })
        : api.post("/users", { username, password, full_name: fullName || null, email: email || null, role, group_scope: scope }),
    onSuccess: () => {
      toast.success("Saqlandi");
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Modal open onClose={onClose} title={user ? `Tahrirlash: ${user.username}` : "Yangi foydalanuvchi"} footer={<Button onClick={() => mut.mutate()} loading={mut.isPending}>Saqlash</Button>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Foydalanuvchi nomi"><Input value={username} onChange={(e) => setUsername(e.target.value)} disabled={!!user} /></Field>
        {!user && (
          <Field label="Parol" hint="Parol siyosatiga mos bo'lishi kerak">
            <Input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        )}
        <Field label="To'liq ism"><Input value={fullName} onChange={(e) => setFullName(e.target.value)} /></Field>
        <Field label="Email"><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <Field label="Rol">
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </Select>
        </Field>
        {user && (
          <div className="flex items-end">
            <Checkbox label="Faol" checked={active} onChange={setActive} />
          </div>
        )}
        <div className="sm:col-span-2">
          <div className="mb-1 text-xs font-medium text-slate-500">Qurilma guruhlari (bo&apos;sh = barcha qurilmalar)</div>
          <div className="flex flex-wrap gap-3">
            {groups.data?.items.map((g) => (
              <Checkbox key={g.id} label={g.name} checked={scope.includes(g.id)} onChange={(v) => setScope((s) => (v ? [...s, g.id] : s.filter((x) => x !== g.id)))} />
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function PasswordForm({ user, self, onClose }: { user: User; self: boolean; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mut = useMutation({
    mutationFn: () => api.post(`/users/${user.id}/password`, { current_password: self ? current : null, new_password: next }),
    onSuccess: async () => {
      toast.success("Parol o'zgartirildi");
      onClose();
      if (self) {
        // Changing your own password revokes existing sessions.
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.assign("/login"); // full reload drops cached client state
      }
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Modal
      open
      onClose={onClose}
      title={`Parol: ${user.username}`}
      footer={<Button onClick={() => mut.mutate()} loading={mut.isPending} disabled={!next || next !== repeat || (self && !current)}>Saqlash</Button>}
    >
      <div className="space-y-3">
        {self && (
          <Field label="Joriy parol">
            <Input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
        )}
        <Field label="Yangi parol">
          <Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Yangi parolni takrorlang" hint={repeat && next !== repeat ? "Parollar mos emas" : undefined}>
          <Input type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
