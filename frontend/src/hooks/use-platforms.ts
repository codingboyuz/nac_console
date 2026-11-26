"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { api } from "@/lib/api";
import type { Platform, PlatformInfo } from "@/lib/types";

export function usePlatforms() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: () => api.get<PlatformInfo[]>("/platforms"),
    staleTime: 5 * 60_000,
  });
}

export function usePlatform(platform: Platform | undefined) {
  const { data } = usePlatforms();
  return useMemo(() => data?.find((p) => p.platform === platform), [data, platform]);
}

export const isHuawei = (p?: string | null) => !!p && p.startsWith("huawei");

/** Read-only quick commands for the terminal, in each platform's CLI dialect. */
export function quickCommands(platform?: string | null): string[] {
  if (isHuawei(platform)) {
    return [
      "display version",
      "display ip interface brief",
      "display interface brief",
      "display vlan",
      "display mac-address",
      "display arp",
      "display cpu-usage",
      "display memory-usage",
      "display stp brief",
      "display ip routing-table",
    ];
  }
  if (platform === "tplink_omada") {
    return ["GET switch", "GET ports", "GET networks", "GET clients", "GET config"];
  }
  if (platform === "tplink_jetstream") {
    return [
      "show system-info",
      "show interface status",
      "show vlan",
      "show mac address-table",
      "show arp",
      "show cpu-utilization",
      "show memory-utilization",
      "show spanning-tree bridge",
      "show ip route",
    ];
  }
  if (platform === "cisco_nxos") {
    return [
      "show version",
      "show interface status",
      "show vlan brief",
      "show mac address-table",
      "show ip arp",
      "show system resources",
      "show spanning-tree",
      "show ip route",
    ];
  }
  return [
    "show version",
    "show ip interface brief",
    "show interfaces status",
    "show vlan brief",
    "show mac address-table",
    "show ip arp",
    "show spanning-tree",
    "show ip route",
  ];
}

export const vendorTone: Record<string, "red" | "sky" | "green"> = {
  Huawei: "red",
  Cisco: "sky",
  "TP-Link": "green",
};
