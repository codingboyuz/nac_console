"""OIDs used by the collector. Huawei entity MIB OIDs are VRP-VERSION-DEPENDENT and are only
queried when the device answers them (missing objects are skipped, never fatal)."""

from __future__ import annotations

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

# IF-MIB ifTable / ifXTable columns
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

# HUAWEI-ENTITY-EXTENT-MIB (hwEntityStateTable)
HW_ENTITY_CPU_USAGE = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5"
HW_ENTITY_MEM_USAGE = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7"
HW_ENTITY_TEMPERATURE = "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.11"
# hwFanStatusTable.hwEntityFanState (1 normal, 2 abnormal)
HW_FAN_STATE = "1.3.6.1.4.1.2011.5.25.31.1.1.10.1.7"
# hwPwrStatusTable.hwEntityPwrState (1 supply, 2 notSupply, 3 sleep, 4 unknown)
HW_POWER_STATE = "1.3.6.1.4.1.2011.5.25.31.1.1.18.1.6"

HUAWEI_ENTERPRISE_PREFIX = "1.3.6.1.4.1.2011"

# CISCO-PROCESS-MIB cpmCPUTotalTable (1-minute / 5-minute busy %)
CISCO_CPU_1MIN = "1.3.6.1.4.1.9.9.109.1.1.1.1.7"
CISCO_CPU_5MIN = "1.3.6.1.4.1.9.9.109.1.1.1.1.8"
# CISCO-MEMORY-POOL-MIB ciscoMemoryPoolUsed / Free (bytes; index 1 = processor pool)
CISCO_MEM_USED = "1.3.6.1.4.1.9.9.48.1.1.1.5"
CISCO_MEM_FREE = "1.3.6.1.4.1.9.9.48.1.1.1.6"
# CISCO-ENVMON-MIB values / states (1 normal, 2 warning, 3 critical, 4 shutdown, 5 notPresent)
CISCO_TEMPERATURE = "1.3.6.1.4.1.9.9.13.1.3.1.3"
CISCO_FAN_STATE = "1.3.6.1.4.1.9.9.13.1.4.1.3"
CISCO_POWER_STATE = "1.3.6.1.4.1.9.9.13.1.5.1.3"
CISCO_ENTERPRISE_PREFIX = "1.3.6.1.4.1.9"
TPLINK_ENTERPRISE_PREFIX = "1.3.6.1.4.1.11863"
# TPLINK-SYSMONITOR-MIB (FAQ 1330; T1600G/T2600G): per-unit CPU 1-minute and memory %
TPLINK_CPU_1MIN = "1.3.6.1.4.1.11863.6.4.1.1.1.1.3"
TPLINK_MEMORY = "1.3.6.1.4.1.11863.6.4.1.2.1.1.2"

IF_TABLE_COLUMNS = {
    "descr": IF_DESCR,
    "name": IF_NAME,
    "alias": IF_ALIAS,
    "admin": IF_ADMIN_STATUS,
    "oper": IF_OPER_STATUS,
    "in_octets": IF_IN_OCTETS,
    "out_octets": IF_OUT_OCTETS,
    "hc_in_octets": IF_HC_IN_OCTETS,
    "hc_out_octets": IF_HC_OUT_OCTETS,
    "in_errors": IF_IN_ERRORS,
    "out_errors": IF_OUT_ERRORS,
    "in_discards": IF_IN_DISCARDS,
    "out_discards": IF_OUT_DISCARDS,
    "high_speed": IF_HIGH_SPEED,
}
