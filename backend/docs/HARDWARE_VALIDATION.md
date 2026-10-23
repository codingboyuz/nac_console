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
