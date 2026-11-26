"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { useCan } from "@/hooks/use-me";
import { isHuawei, usePlatforms } from "@/hooks/use-platforms";
import { api, errorMessage } from "@/lib/api";
import { P } from "@/lib/perms";
import type { Credential, Device, DeviceCreate, DeviceGroup, Page, Platform } from "@/lib/types";
import { Alert, Button, Checkbox, Field, FormSection, Input, Modal, Select } from "./ui";

const splitList = (s: string) =>
  s
    .split(/[,\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);

export function DeviceForm({ open, onClose, device }: { open: boolean; onClose: () => void; device?: Device }) {
  const qc = useQueryClient();
  const can = useCan();
  const creds = useQuery({
    queryKey: ["credentials", "all"],
    queryFn: () => api.get<Page<Credential>>("/credentials", { limit: 500 }),
    enabled: open,
  });
  const platforms = usePlatforms();
  const groups = useQuery({
    queryKey: ["device-groups"],
    queryFn: () => api.get<Page<DeviceGroup>>("/device-groups", { limit: 500 }),
    enabled: open,
  });

  const [f, setF] = useState(() => ({
    hostname: device?.hostname ?? "",
    mgmt_ip: device?.mgmt_ip ?? "",
    platform: (device?.platform ?? "huawei_vrp") as Platform,
    protocol: device?.protocol ?? "ssh",
    port: device?.port ? String(device.port) : "",
    family: device?.family ?? "UNKNOWN",
    model: device?.model ?? "",
    credential_id: device?.credential_id ?? "",
    snmp_credential_id: device?.snmp_credential_id ?? "",
    group_id: device?.group_id ?? "",
    site: device?.site ?? "",
    description: device?.description ?? "",
    tags: device?.tags.join(", ") ?? "",
    mgmt_interface: device?.mgmt_interface ?? "",
    mgmt_vlan: device?.mgmt_vlan ? String(device.mgmt_vlan) : "",
    uplink_interfaces: device?.uplink_interfaces.join(", ") ?? "",
    timeout: String(device?.timeout ?? 30),
    snmp_port: String(device?.snmp_port ?? 161),
    requires_super: device?.requires_super ?? false,
    allow_insecure_protocol: false,
  }));
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((s) => ({ ...s, [k]: v }));

  const mut = useMutation({
    mutationFn: () => {
      const body: DeviceCreate = {
        hostname: f.hostname.trim(),
        mgmt_ip: f.mgmt_ip.trim(),
        platform: f.platform,
        protocol: f.protocol as DeviceCreate["protocol"],
        port: f.port ? Number(f.port) : null,
        family: (huawei ? f.family : "UNKNOWN") as DeviceCreate["family"],
        model: f.model || null,
        credential_id: f.credential_id || null,
        snmp_credential_id: f.snmp_credential_id || null,
        group_id: f.group_id || null,
        site: f.site || null,
        description: f.description || null,
        tags: splitList(f.tags),
        mgmt_interface: f.mgmt_interface || null,
        mgmt_vlan: f.mgmt_vlan ? Number(f.mgmt_vlan) : null,
        uplink_interfaces: splitList(f.uplink_interfaces),
        timeout: Number(f.timeout) || 30,
        snmp_port: Number(f.snmp_port) || 161,
        requires_super: f.requires_super,
        allow_insecure_protocol: f.allow_insecure_protocol,
      };
      return device ? api.patch<Device>(`/devices/${device.id}`, body) : api.post<Device>("/devices", body);
    },
    onSuccess: (d) => {
      toast.success(device ? "Qurilma yangilandi" : `Qurilma qo'shildi: ${d.hostname}`);
      if (d.security_warning) toast.warning(d.security_warning);
      qc.invalidateQueries({ queryKey: ["devices"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    mut.mutate();
  };

  const huawei = isHuawei(f.platform);
  const selected = platforms.data?.find((p) => p.platform === f.platform);
  const transports = selected?.transports ?? ["ssh", "telnet", "netconf", "snmp"];
  const viaApi = f.protocol === "api";
  const vendors = [...new Set((platforms.data ?? []).map((p) => p.vendor))];
  const changePlatform = (value: Platform) => {
    const info = platforms.data?.find((p) => p.platform === value);
    setF((s) => ({
      ...s,
      platform: value,
      family: isHuawei(value) ? s.family : "UNKNOWN",
      protocol:
        info && !info.transports.includes(s.protocol)
          ? ((info.transports.includes("ssh") ? "ssh" : info.transports[0]) as typeof s.protocol)
          : s.protocol,
    }));
  };
  const cliCreds = creds.data?.items.filter((c) => c.kind === (viaApi ? "omada_api" : "cli")) ?? [];
  const snmpCreds = creds.data?.items.filter((c) => c.kind.startsWith("snmp")) ?? [];

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={device ? `Tahrirlash: ${device.hostname}` : "Yangi qurilma"}
      wide
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Bekor qilish
          </Button>
          <Button type="submit" form="device-form" loading={mut.isPending}>
            Saqlash
          </Button>
        </>
      }
    >
      <form id="device-form" onSubmit={submit} className="space-y-5">
        <FormSection title="Asosiy ma'lumotlar" description="Qurilma nomi, manzili va platformasi">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Hostname *">
              <Input value={f.hostname} onChange={(e) => set("hostname", e.target.value)} required />
            </Field>
            <Field label="Boshqaruv IP *">
              <Input value={f.mgmt_ip} onChange={(e) => set("mgmt_ip", e.target.value)} required placeholder="192.0.2.10" />
            </Field>
            <Field label="Platforma (vendor)">
              <Select value={f.platform} onChange={(e) => changePlatform(e.target.value as Platform)}>
                {vendors.map((vendor) => (
                  <optgroup key={vendor} label={vendor}>
                    {platforms.data
                      ?.filter((p) => p.vendor === vendor)
                      .map((p) => (
                        <option key={p.platform} value={p.platform} disabled={!p.available}>
                          {p.label}
                          {p.available ? "" : " — tez orada"}
                        </option>
                      ))}
                  </optgroup>
                ))}
              </Select>
            </Field>
            {huawei ? (
              <Field label="Oila (Huawei)" hint="CE tanlansa VRP V8 ishlatiladi">
                <Select value={f.family} onChange={(e) => set("family", e.target.value as typeof f.family)}>
                  {["UNKNOWN", "S", "CE", "AR"].map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </Select>
              </Field>
            ) : (
              <Field label="Rollback taymeri">
                <Input
                  disabled
                  value={
                    selected?.rollback_timer === "none"
                      ? "Yo'q — lockout override taqiqlangan"
                      : selected?.rollback_timer === "commit-trial"
                        ? "configure terminal revert timer"
                        : selected?.rollback_timer === "scheduled-reload"
                          ? "reboot-schedule (saqlangan konfiguratsiyaga qaytish)"
                          : "—"
                  }
                />
              </Field>
            )}
            <Field label="Model">
              <Input value={f.model} onChange={(e) => set("model", e.target.value)} placeholder="S5735-L48T4X" />
            </Field>
            <Field label="Sayt">
              <Input value={f.site} onChange={(e) => set("site", e.target.value)} />
            </Field>
          </div>
        </FormSection>
        <FormSection title="Ulanish" description="Qaysi protokol va credential bilan boshqariladi">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Protokol">
              <Select value={f.protocol} onChange={(e) => set("protocol", e.target.value as typeof f.protocol)}>
                {["ssh", "api", "netconf", "snmp", "telnet"]
                  .filter((x) => transports.includes(x))
                  .map((x) => (
                    <option key={x}>{x}</option>
                  ))}
              </Select>
            </Field>
            <Field label="Port" hint="Bo'sh = protokol bo'yicha standart">
              <Input type="number" value={f.port} onChange={(e) => set("port", e.target.value)} />
            </Field>
            <Field label="Timeout (s)">
              <Input type="number" value={f.timeout} onChange={(e) => set("timeout", e.target.value)} />
            </Field>
            <Field
              label={viaApi ? "Omada API credential" : "CLI credential"}
              hint={viaApi ? "Boshqaruv IP = switchning o'z IP manzili" : undefined}
            >
              <Select value={f.credential_id} onChange={(e) => set("credential_id", e.target.value)}>
                <option value="">—</option>
                {cliCreds.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="SNMP credential">
              <Select value={f.snmp_credential_id} onChange={(e) => set("snmp_credential_id", e.target.value)}>
                <option value="">—</option>
                {snmpCreds.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.kind})
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="SNMP port">
              <Input type="number" value={f.snmp_port} onChange={(e) => set("snmp_port", e.target.value)} />
            </Field>
          </div>
          <div className="flex flex-wrap gap-5 mt-3">
            <Checkbox
              label={huawei ? "super parol talab qilinadi" : "enable secret talab qilinadi (credential'dagi super paroli)"}
              checked={f.requires_super}
              onChange={(v) => set("requires_super", v)}
            />
            {f.protocol === "telnet" && (
              <Checkbox
                label="Xavfsiz bo'lmagan protokolga ruxsat (Telnet)"
                checked={f.allow_insecure_protocol}
                onChange={(v) => set("allow_insecure_protocol", v)}
                disabled={!can(P.INSECURE_PROTOCOL)}
              />
            )}
          </div>
          {f.protocol === "telnet" && (
            <div className="mt-3">
              <Alert tone="warn">Telnet parollarni ochiq matnda uzatadi. Faqat izolyatsiyalangan boshqaruv tarmog&apos;ida ishlating.</Alert>
            </div>
          )}
        </FormSection>
        <FormSection title="Lockout himoyasi" description="Boshqaruv yo'lini kesib qo'yadigan o'zgarishlar shu ma'lumotlar bo'yicha bloklanadi">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Boshqaruv interfeysi" hint="Lockout himoyasi uchun">
              <Input value={f.mgmt_interface} onChange={(e) => set("mgmt_interface", e.target.value)} placeholder={huawei ? "Vlanif100" : "Vlan100"} />
            </Field>
            <Field label="Boshqaruv VLAN">
              <Input type="number" value={f.mgmt_vlan} onChange={(e) => set("mgmt_vlan", e.target.value)} />
            </Field>
            <Field label="Uplink portlar" hint="Vergul bilan">
              <Input value={f.uplink_interfaces} onChange={(e) => set("uplink_interfaces", e.target.value)} placeholder={huawei ? "XGE0/0/1, XGE0/0/2" : f.platform === "cisco_nxos" ? "Ethernet1/49" : "Te1/1/1"} />
            </Field>
          </div>
        </FormSection>
        <FormSection title="Tashkiliy" description="Guruh, teglar va izoh">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Guruh">
              <Select value={f.group_id} onChange={(e) => set("group_id", e.target.value)}>
                <option value="">—</option>
                {groups.data?.items.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Teglar" hint="Vergul bilan">
              <Input value={f.tags} onChange={(e) => set("tags", e.target.value)} />
            </Field>
            <Field label="Tavsif">
              <Input value={f.description} onChange={(e) => set("description", e.target.value)} />
            </Field>
          </div>
        </FormSection>
      </form>
    </Modal>
  );
}
