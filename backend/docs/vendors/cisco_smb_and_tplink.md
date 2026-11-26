# Cisco SMB and TP-Link JetStream: verified CLI syntax (input for phases 16-17)

Collected from the vendors' official CLI reference guides on 2026-11-25. None of the guides
contains real command *output* for TP-Link, and SMB output examples are partial: parsers for
these platforms need fixtures captured from real devices (see HARDWARE_VALIDATION.md).

## Cisco Small Business (CBS350 / SG350 / SG550X; SG300 differences noted)

Sources: Sx350X Ph. 2.5.10 CLI Reference Guide (2023), Cisco Business 350 CLI Guide (HTML,
2025), SG300 CLI Guide 1.3.5, Netmiko `cisco_s300` driver.

| Topic | Syntax |
|---|---|
| Session | Netmiko `cisco_s300`; `terminal datadump` disables paging; prompts `host>`, `host#`, `host(config)#`, `host(config-if)#` |
| Identity | `show version` (= `show bootvar`), `show system` (System Description / Up Time / Name), `show inventory` |
| VLANs | `show vlan` (columns Tagged Ports / UnTagged Ports, ranges like `te1/0/2-4`); create `vlan 10` (global on 2.x/3.x; only inside `vlan database` on SG300); name via `interface vlan 10` + `name X`; delete `no vlan 10` |
| Ports | `switchport mode access|trunk|general`; `switchport access vlan N`; `switchport trunk allowed vlan {all|none|list|add|remove|except}` (SG300: add/remove only); `switchport trunk native vlan N`; general: `switchport general allowed vlan add L [tagged|untagged]`, `switchport general pvid N` |
| Interface names | `gi1/0/1`, `te1/0/1`, `po1`, `vlan N` (SG300: flat `gi1`, `fa1`, `po1`) |
| Status | `show interfaces status` (Port Type Duplex Speed Neg Flow-ctrl Link-State ...), `show interfaces counters`, `show running-config interface gi1/0/1` (blocks end with `exit`, not indented) |
| Tables | `show mac address-table` (colon MACs, port `0`/type `self` = switch), `show arp`, `show ip route`, `show ip dhcp snooping binding` (dotted MACs) |
| STP | `spanning-tree mode stp|rstp|mst|pvst|rapid-pvst` (SG300: stp/rstp/mst), `spanning-tree priority N`, `spanning-tree portfast` |
| DHCP snooping | `ip dhcp snooping`, `ip dhcp snooping vlan N`, interface `ip dhcp snooping trust` |
| ACL | `ip access-list extended NAME`; entries `permit|deny <proto> src wc [sport] dst wc [dport] [ace-priority N] [log-input]` (no `host`, use wildcard 0.0.0.0); delete one entry by repeating it with `no`; bind `service-acl input NAME` (one per port, `no service-acl input` first) |
| Static MAC | `mac address-table static <mac> vlan N interface <if>`; no drop/blackhole entry (use a MAC ACL) |
| Routing | `ip route <prefix> <mask|/len> <nh> [metric N]`; RIP on CBS350; no OSPF; VRRP only on SG550X |
| 802.1X / MAB | `dot1x system-auth-control`, `aaa authentication dot1x default radius`, `radius-server host IP key K`, per port `dot1x authentication 802.1x mac`, `dot1x port-control auto`, guest VLAN via `interface vlan N` + `dot1x guest-vlan`, dynamic VLAN `dot1x radius-attributes vlan`; **no CoA** |
| Save | `write memory` -> `Overwrite file [startup-config] ?[Yes/press any key for no]` |
| Errors | `% bad parameter value` (documented); `% Unrecognized command`, `% Wrong number of parameters` (community) |
| Rollback timer | none (mirror-config only) -> `RollbackTimer.NONE` |
| CPU / memory | `show cpu utilization`; no memory command (use SNMP) |

## TP-Link JetStream / Omada managed switches (T1600G, T2600G, SG3428/X, TL-SG34xx)

Sources: T2600G CLI Reference Guide REV4.2.0 (2019), JetStream CLI Reference Guide REV5.0.0
(2022), Omada Managed Switches CLI Reference Guide REV1.0.0 (2025), TP-Link FAQ 1330 (SNMP).

| Topic | Syntax |
|---|---|
| Session | Netmiko `tplink_jetstream`; `enable` then `configure`; prompts `Switch>`, `Switch#`, `Switch(config)#`, `Switch(config-if)#`, `Switch(config-vlan)#` |
| Identity | `show system-info` (Hardware Version, Firmware Version, Run Time) |
| VLANs | `show vlan [id N]`, `show vlan brief`; create `vlan 2-10,100` (enters config-vlan), `name X` (16 chars), `no vlan N` |
| Ports | **no `switchport mode`**: every port is "general": `switchport general allowed vlan L tagged|untagged`, `no switchport general allowed vlan L`, `switchport pvid N`; per-port membership `show interface switchport gigabitEthernet 1/0/1` |
| Interface names | `interface gigabitEthernet 1/0/1`, `ten-gigabitEthernet 1/0/25`, `port-channel 1`, `interface range gigabitEthernet 1/0/1-3` |
| Status | `show interface status`, `show interface counters`, `show running-config interface gigabitEthernet 1/0/1` (2022+ guides) |
| Tables | `show mac address-table [dynamic|static|filtering]` (no `all`), `show arp`, `show ip route`, DHCP bindings `show ip source binding` |
| STP | `spanning-tree`, `spanning-tree mode stp|rstp|mstp`, `spanning-tree priority N`, port edge `spanning-tree common-config portfast enable` |
| DHCP snooping | `ip dhcp snooping`, `ip dhcp snooping vlan L`, interface `ip dhcp snooping trust` (not on 2019 T2600G firmware) |
| ACL | `access-list create <id> [name N]` (IP ids 500-999); `access-list ip <id> rule <n> {permit|deny} logging disable sip A sip-mask M dip A dip-mask M [protocol N d-port P d-port-mask 65535]`; `no access-list ip <id> rule <n>`; bind `access-list bind <id> interface gigabitEthernet <ports>` (ingress) |
| Static MAC | `mac address-table static aa:bb:cc:dd:ee:ff vid N interface gigabitEthernet 1/0/1`; blackhole `mac address-table filtering <mac> vid N` |
| Routing | `ip route <dest> <mask> <nh> [distance]`; OSPF/RIP/VRRP only on some 2025-era models |
| 802.1X / MAB | `dot1x system-auth-control`, `dot1x vlan-assignment`, `radius-server host IP auth-port A acct-port B key K`, `aaa group radius NAME` + `server IP`, `aaa authentication dot1x default NAME`, per port `dot1x`, `dot1x port-control auto`, `dot1x port-method mac-based`, `dot1x mab`, `dot1x guest-vlan`; **no CoA documented** |
| Save | `copy running-config startup-config` |
| Rollback timer | `reboot-schedule in <minutes>` / `reboot-schedule cancel` (reboots into the saved config) -> `RollbackTimer.SCHEDULED_RELOAD` |
| CPU / memory | `show cpu-utilization`, `show memory-utilization`; SNMP 1.3.6.1.4.1.11863.6.4.1.1.1.1.{2,3,4} (CPU 5s/1m/5m), 1.3.6.1.4.1.11863.6.4.1.2.1.1.2 (memory) |
