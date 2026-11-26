# Hardware validation checklist

Everything below was verified only against mocks/simulators on the build host. Tick each item
on a real, authorized lab device before production use. Note the device model and VRP version.

## Transport / drivers
- [ ] S-series (V200R0xx): Netmiko `huawei` login, `screen-length 0 temporary`, `save` confirmation prompt wording.
- [ ] CE-series (VRP V8): `huawei_vrpv8` prompts `[~HOST]` / `[*HOST]`, `commit`, `commit trial <s>` + confirming `commit`, `abort` discards candidate.
- [ ] AR router: family detection from `display version`.
- [ ] `super` privilege escalation prompt text on devices with `requires_super`.
- [ ] Telnet login prompts (`Username:` / `Password:`) and error text on bad credentials.
- [ ] SSH host-key TOFU, then key rotation after device re-key (`stelnet server` key regeneration).
- [ ] VTY exhaustion: confirm `DEVICE_MAX_SESSIONS=2` never exceeds `user-interface maximum-vty`.
- [ ] NETCONF: capabilities list, `huaweiyang` device handler, confirmed-commit timeout semantics, `cancel-commit` support.
- [ ] S-series NETCONF legacy schema (`http://www.huawei.com/netconf/vrp`) payloads.
- [ ] SNMPv3 authPriv (SHA/AES128) against `snmp-agent usm-user v3`.
- [ ] Huawei entity MIB OIDs (CPU/memory/temperature/fan/power) on each platform.

## Parsers (compare with real captures, add them as fixtures)
- [ ] display version / interface brief / interface / ip interface brief
- [ ] display vlan (S and CE layouts), display mac-address, display arp
- [ ] display ip routing-table (ECMP), display stp / stp brief
- [ ] display dhcp snooping user-bind all, cpu-usage, memory-usage
- [ ] display transceiver verbose, display lldp neighbor brief
- [ ] Error message wording: `Error: Unrecognized command`, `Wrong parameter`, `Incomplete command`, CE commit failures.

## Backups / restore
- [ ] `display saved-configuration` availability and output on S and CE.
- [ ] Restore merge plan on a lab device (VLAN batch diff, interface body undo, static routes).
- [ ] `schedule reboot delay` + `undo schedule reboot` prompts on S-series; `commit trial` on CE.

## NAC / RADIUS
- [ ] Generated provisioning template on S5735 (V200R019) and CE (V8): unified-mode requirement, `authentication event` options, `dot1x enable` on CE.
- [ ] Huawei attribute formats: Calling-Station-Id MAC format, NAS-Port-Id layout, MAB User-Name/Password.
- [ ] Dynamic VLAN via Tunnel-* attributes, Filter-Id -> ACL mapping (`acl` UCL vs number).
- [ ] CoA: `radius-server authorization` + Disconnect-ACK/NAK and CoA-ACK behaviour.
- [ ] FreeRADIUS 3.2 rlm_rest JSON body/response handling with the templates in deploy/freeradius.

## Cisco IOS / IOS-XE (Catalyst)
- [ ] Netmiko `cisco_ios`: enable with the credential's super password; prompt detection on stacks.
- [ ] 'configure terminal revert timer N' + 'configure confirm' with the archive configured; behaviour without archive.
- [ ] Parsers vs real captures: show version (IOS 15.2 and IOS-XE 17), show interfaces status / interfaces, show vlan brief, show mac address-table, show ip arp, show ip route (VRF, ECMP), show spanning-tree, show processes cpu/memory, show ip access-lists.
- [ ] Named ACL sequence handling and canonical comparison (port names, wildcard bits).
- [ ] 802.1X/MAB template on IOS 15.2 and IOS-XE (legacy vs IBNS 2.0 new-style); 'aaa new-model' login methods.
- [ ] CoA (dynamic-author) disconnect / reauth; Calling-Station-Id format.
- [ ] VRRP syntax on IOS-XE 16+ ('fhrp version vrrp v3').

## Cisco NX-OS (Nexus)
- [ ] Netmiko `cisco_nxos` login and 'copy running-config startup-config' prompts.
- [ ] Parsers vs real captures: show interface, show interface status, show ip route, show mac address-table, show system resources, show ip access-lists.
- [ ] Per-interface OSPF, BGP neighbour sub-mode, VRRP, port vs routed ACL application.
- [ ] dot1x / MAC bypass availability per Nexus model.

## Cisco Small Business (CBS / SG)
- [ ] Netmiko `cisco_s300` login, 'write' confirmation prompt, paging on CBS250 vs CBS350.
- [ ] Parsers vs real captures: show version / show system, show vlan, show interfaces status / counters, show mac address-table, show arp, show ip dhcp snooping binding, show ip route, show spanning-tree, show cpu utilization, show access-lists.
- [ ] General mode as hybrid; 'vlan database' on SG300 firmware 1.4.
- [ ] ace-priority ACLs + 'service-acl input'; management access-class.

## TP-Link JetStream / Omada switches (CLI)
- [ ] Replace every synthetic fixture in tests/fixtures/tplink/t2600g_v3 with real output (T1600G, T2600G, SG3428).
- [ ] Netmiko `tplink_jetstream`: enable / enable-admin with the super password; buffered prompts after 'exit'.
- [ ] 'reboot-schedule in N' / 'reboot-schedule cancel' on the target firmware; confirm a pending reboot is really cancelled.
- [ ] 'switchport general allowed vlan' + 'switchport pvid' semantics; ACL ids and 'access-list bind'.
- [ ] 802.1X 'dot1x vlan-assignment', MAB, guest VLAN syntax; absence of CoA.
- [ ] SNMP TPLINK-SYSMONITOR-MIB OIDs on stacked units.

## TP-Link Omada Controller (Open API)
- [x] Token: POST /openapi/authorize/token?grant_type=client_credentials, 'Authorization: AccessToken=...'.
- [x] GET /openapi/v1/{omadacId}/sites.
- [ ] Device list (/sites/{siteId}/devices): field names ip, type, mac, model, firmwareVersion, uptimeLong, cpuUtil, memUtil.
- [ ] Switch ports (/switches/{mac}/ports) and modify (PATCH /ports/{port}): disable, name, profileOverrideEnable, nativeNetworkId, tagNetworkIds; linkSpeed codes.
- [ ] LAN networks (/lan-networks): create body (name, purpose, vlan), rename, delete.
- [ ] Clients (/clients): switchMac, port, vid.
- [ ] Controller versions 5.x vs 6.x (OC200/OC300/software) and port 443 vs 8043.
- All unverified paths live in `PATHS` in app/network/drivers/omada_api.py.
