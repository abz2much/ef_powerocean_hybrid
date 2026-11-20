"""The entities of a PowerOcean Single Phase (cloud model 86), one per value.

Each entry names where its value comes from:
- joined: the same measurement in both sources; Modbus first, the cloud as backup.
- counter: an energy counter in both sources; one fixed source, chosen at setup.
- modbus: only Modbus has it.
- cloud: only the cloud has it, or the cloud's twin of a Modbus value that is not
  proven to agree yet (kept as its own entity until it is).

Cloud values are located in ParseResult.values() of the cloud parser, whose keys
are "{serial}_{model prefix}_{report}_{key}", or "{serial}_{key}" at the top level.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)

from .cloud.datapoints import DISABLED_BY_DEFAULT, TRANSLATED_STATES
from .const import ENERGY_SENSOR_MAP, SENSOR_MAP
from .models import EnergySensorDef, InverterModel, SensorDef

type Kind = Literal["joined", "counter", "modbus", "cloud"]

STREAM_TIME: Final = "energyStreamUpdateTime"
HEARTBEAT_TIME: Final = "heartbeatUpdateTime"

HEARTBEAT: Final = "EMS_HEARTBEAT"
CHANGE: Final = "EMS_CHANGE_REPORT"
STREAM: Final = "ENERGY_STREAM_REPORT"
PACK: Final = "BP_STA_REPORT"
MPPT: Final = "mpptHeartBeat_"


@dataclass(frozen=True)
class CloudSource:
    """Where a value is in the cloud response."""

    key: str
    # Report name without the model prefix (JTS1_, JT303_); None at the top level.
    report: str | None = None
    # Applied to numbers: 0.001 turns Wh into kWh, -1 flips a sign.
    factor: float = 1
    # The report time that shows how fresh the value is (joined values only);
    # None: fresh while the cloud's polls succeed.
    time_key: str | None = None


@dataclass(frozen=True)
class CatalogueEntry:
    """One entity and the sources of its value."""

    key: str
    kind: Kind
    description: SensorDef | EnergySensorDef
    modbus_key: str | None = None
    cloud: CloudSource | None = None
    # One entity per battery pack, on the pack's device.
    per_pack: bool = False
    enabled: bool = True

    @property
    def translated_states(self) -> bool:
        """Whether the state is shown in lowercase, named by the translations.

        As powerocean_single_phase does for these datapoints.
        """
        return (
            self.kind == "cloud"
            and self.cloud is not None
            and parser_key(self.cloud) in TRANSLATED_STATES
        )


_MODEL_PREFIX = re.compile(r"(?:[A-Z0-9]+_)?")


def parser_key(source: CloudSource) -> str:
    """Return the parser's key of the datapoint, as the cloud integration's lists
    name it (the heartbeat's MPPT values without their group)."""
    return source.key.removeprefix(MPPT)


def cloud_value(values: dict[str, Any], serial: str, source: CloudSource) -> Any:
    """Return the value *source* names for the device *serial*, None if absent."""
    if source.report is None:
        raw = values.get(f"{serial}_{source.key}")
    else:
        head, tail = f"{serial}_", f"{source.report}_{source.key}"
        raw = next(
            (
                value
                for unique_id, value in values.items()
                if unique_id.startswith(head)
                and unique_id.endswith(tail)
                and _MODEL_PREFIX.fullmatch(unique_id[len(head) : -len(tail)])
            ),
            None,
        )
    return _scaled(raw, source.factor)


def _scaled(raw: Any, factor: float) -> Any:
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        try:
            number = float(raw)
        except ValueError:
            return raw if factor == 1 else None
    else:
        number = raw
    return number * factor if factor != 1 else number


_SINGLE = InverterModel.POWEROCEAN_SINGLE_PHASE
_MODBUS_DEFS: Final = {
    d.key: d for d in SENSOR_MAP if _SINGLE not in d.unsupported_models
} | {d.key: d for d in ENERGY_SENSOR_MAP}

W, V, A = UnitOfPower.WATT, UnitOfElectricPotential.VOLT, UnitOfElectricCurrent.AMPERE
MEASUREMENT = SensorStateClass.MEASUREMENT
DIAG = EntityCategory.DIAGNOSTIC


def _sensor(
    key: str,
    unit: str | None = None,
    device_class: SensorDeviceClass | None = None,
    *,
    state_class: SensorStateClass | None = MEASUREMENT,
    category: EntityCategory | None = None,
) -> SensorDef:
    if unit is None and device_class is None:
        state_class = None
    return SensorDef(
        key=key,
        unit=unit,
        device_class=device_class,
        state_class=state_class,
        entity_category=category,
    )


def _power(key: str, category: EntityCategory | None = None) -> SensorDef:
    return _sensor(key, W, SensorDeviceClass.POWER, category=category)


def _cloud(
    key: str,
    source: CloudSource,
    description: SensorDef,
    *,
    per_pack: bool = False,
    enabled: bool | None = None,
) -> CatalogueEntry:
    """A value only the cloud has; on or off by default as the cloud
    integration decides it (DISABLED_BY_DEFAULT), unless *enabled* says."""
    if enabled is None:
        enabled = parser_key(source) not in DISABLED_BY_DEFAULT
    return CatalogueEntry(
        key, "cloud", description, cloud=source, per_pack=per_pack, enabled=enabled
    )


def _text(
    key: str,
    source: CloudSource,
    *,
    enabled: bool | None = None,
    per_pack: bool = False,
) -> CatalogueEntry:
    return _cloud(
        key, source, _sensor(key, category=DIAG), enabled=enabled, per_pack=per_pack
    )


_JOINED: Final = (
    ("house_power", CloudSource("sysLoadPwr", time_key=STREAM_TIME)),
    ("battery_power", CloudSource("bpPwr", time_key=STREAM_TIME)),
    ("grid_power", CloudSource("sysGridPwr", time_key=STREAM_TIME)),
    # As the cloud integration reads it. The report is sent only when a value
    # changes, so it counts while the cloud's polls succeed, not by its time.
    ("battery_soc", CloudSource("bpSoc", CHANGE)),
    ("voltage_l1", CloudSource("pcsAPhase_vol", HEARTBEAT, time_key=HEARTBEAT_TIME)),
    ("frequency", CloudSource("pcsAcFreq", HEARTBEAT, time_key=HEARTBEAT_TIME)),
)
_COUNTERS: Final = (
    ("bat_charged_total", CloudSource("bpTotalChgEnergy", CHANGE, factor=0.001)),
    ("bat_discharged_total", CloudSource("bpTotalDsgEnergy", CHANGE, factor=0.001)),
)


def _pv(n: int) -> tuple[CatalogueEntry, ...]:
    return (
        _cloud(
            f"cloud_pv{n}_voltage",
            CloudSource(f"{MPPT}mpptPv{n}_vol", HEARTBEAT),
            _sensor(
                f"cloud_pv{n}_voltage", V, SensorDeviceClass.VOLTAGE, category=DIAG
            ),
        ),
        _cloud(
            f"cloud_pv{n}_current",
            CloudSource(f"{MPPT}mpptPv{n}_amp", HEARTBEAT),
            _sensor(
                f"cloud_pv{n}_current", A, SensorDeviceClass.CURRENT, category=DIAG
            ),
        ),
        _cloud(
            f"cloud_pv{n}_power",
            CloudSource(f"{MPPT}mpptPv{n}_pwr", HEARTBEAT),
            _power(f"cloud_pv{n}_power"),
        ),
        _text(
            f"pv{n}_light",
            CloudSource(f"{MPPT}mpptPv{n}_lightSta", HEARTBEAT),
            enabled=False,
        ),
    )


_FLOWS: Final = (
    "batteryToGrid",
    "batteryToHouse",
    "gridToBattery",
    "gridToHouse",
    "solarToBattery",
    "solarToGrid",
    "solarToHouse",
)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


_INVERTER_CLOUD: Final = (
    # The heartbeat's own grid and battery power: about once a minute, and not
    # proven to match Modbus, so they are diagnostics, not backups.
    _cloud(
        "pcs_meter_power",
        CloudSource("pcsMeterPower", HEARTBEAT),
        _power("pcs_meter_power", DIAG),
    ),
    _cloud(
        "ems_battery_power",
        CloudSource("emsBpPower", HEARTBEAT),
        _power("ems_battery_power", DIAG),
    ),
    # Twins of Modbus values, separate until a recorded comparison passes.
    _cloud(
        "cloud_solar_power",
        CloudSource(f"{MPPT}mpptPv_pwrTotal", HEARTBEAT),
        _power("cloud_solar_power"),
    ),
    *_pv(1),
    *_pv(2),
    _cloud(
        "cloud_current_l1",
        CloudSource("pcsAPhase_amp", HEARTBEAT),
        _sensor("cloud_current_l1", A, SensorDeviceClass.CURRENT, category=DIAG),
    ),
    # Cloud-only values keep the cloud's sign and unit (positive when feeding AC),
    # so dashboards on them carry on unchanged.
    _cloud(
        "cloud_inverter_ac_power",
        CloudSource("pcsActPwr", HEARTBEAT),
        _power("cloud_inverter_ac_power", DIAG),
    ),
    _cloud(
        "cloud_active_power_l1",
        CloudSource("pcsAPhase_actPwr", HEARTBEAT),
        _power("cloud_active_power_l1", DIAG),
    ),
    _cloud(
        "cloud_reactive_power_l1",
        CloudSource("pcsAPhase_reactPwr", HEARTBEAT),
        _sensor(
            "cloud_reactive_power_l1",
            UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            SensorDeviceClass.REACTIVE_POWER,
            category=DIAG,
        ),
        enabled=False,
    ),
    _cloud(
        "cloud_apparent_power_l1",
        CloudSource("pcsAPhase_apparentPwr", HEARTBEAT),
        _sensor(
            "cloud_apparent_power_l1",
            UnitOfApparentPower.VOLT_AMPERE,
            SensorDeviceClass.APPARENT_POWER,
            category=DIAG,
        ),
        enabled=False,
    ),
    _cloud(
        "active_power_limit_up",
        CloudSource("pcsActivePowerLimitUp", HEARTBEAT),
        _power("active_power_limit_up", DIAG),
        enabled=False,
    ),
    _cloud(
        "active_power_limit_down",
        CloudSource("pcsActivePowerLimitDn", HEARTBEAT),
        _power("active_power_limit_down", DIAG),
        enabled=False,
    ),
    _cloud(
        "cloud_battery_count",
        CloudSource("bpOnlineSum", CHANGE),
        _sensor("cloud_battery_count", category=DIAG),
    ),
    _cloud(
        "cloud_battery_remaining",
        CloudSource("bpRemainWatth", HEARTBEAT),
        _sensor(
            "cloud_battery_remaining",
            UnitOfEnergy.WATT_HOUR,
            SensorDeviceClass.ENERGY_STORAGE,
        ),
    ),
    *(
        _cloud(
            _snake(flow), CloudSource(f"{MPPT}{flow}", HEARTBEAT), _power(_snake(flow))
        )
        for flow in _FLOWS
    ),
    _cloud(
        "insulation_resistance",
        CloudSource(f"{MPPT}mpptInsResist", HEARTBEAT),
        _sensor("insulation_resistance", "Ω", category=DIAG),
    ),
    _text(
        "ems_battery_selfcheck_state",
        CloudSource("emsBpSelfcheckState", HEARTBEAT),
        enabled=False,
    ),
    _text(
        "ems_mppt_run_state", CloudSource("emsMpptRunState", HEARTBEAT), enabled=False
    ),
    _text(
        "ems_mppt_selfcheck_state",
        CloudSource("emsMpptSelfcheckState", HEARTBEAT),
        enabled=False,
    ),
    _cloud(
        "heartbeat_update_time",
        CloudSource(HEARTBEAT_TIME, HEARTBEAT),
        _sensor(
            "heartbeat_update_time",
            device_class=SensorDeviceClass.TIMESTAMP,
            state_class=None,
            category=DIAG,
        ),
    ),
    _cloud(
        "energy_stream_update_time",
        CloudSource(STREAM_TIME, STREAM),
        _sensor(
            "energy_stream_update_time",
            device_class=SensorDeviceClass.TIMESTAMP,
            state_class=None,
            category=DIAG,
        ),
        enabled=False,
    ),
    _cloud(
        "ems_change_update_time",
        CloudSource("emsChangeUpdateTime", CHANGE),
        _sensor(
            "ems_change_update_time",
            device_class=SensorDeviceClass.TIMESTAMP,
            state_class=None,
            category=DIAG,
        ),
        enabled=False,
    ),
    _text("cloud_operating_mode", CloudSource("emsWordMode", CHANGE)),
    _text("pcs_run_state", CloudSource("pcsRunSta", CHANGE)),
    _cloud(
        "cloud_feed_in_power",
        CloudSource("emsFeedPwr", CHANGE),
        _power("cloud_feed_in_power", DIAG),
    ),
    _text("cloud_feed_mode", CloudSource("emsFeedMode", CHANGE), enabled=False),
    _cloud(
        "cloud_feed_ratio",
        CloudSource("emsFeedRatio", CHANGE),
        _sensor("cloud_feed_ratio", PERCENTAGE, category=DIAG),
        enabled=False,
    ),
    _cloud(
        "keep_soc",
        CloudSource("emsKeepSoc", CHANGE),
        _sensor("keep_soc", PERCENTAGE, category=DIAG),
        enabled=False,
    ),
    _cloud(
        "backup_ratio",
        CloudSource("sysBatBackupRatio", CHANGE),
        _sensor("backup_ratio", PERCENTAGE, category=DIAG),
        enabled=False,
    ),
    _text(
        "battery_limit_reason", CloudSource("batteryLimitReason", CHANGE), enabled=False
    ),
    _text("pcs_ac_error_code", CloudSource("pcsAcErrCode", CHANGE), enabled=False),
    _text("pcs_dc_error_code", CloudSource("pcsDcErrCode", CHANGE), enabled=False),
    _text(
        "pcs_power_derating_flag",
        CloudSource("pcsPowerDeratingFlag", CHANGE),
        enabled=False,
    ),
    _text(
        "pcs_power_derating_set",
        CloudSource("pcsPowerDeratingSet", CHANGE),
        enabled=False,
    ),
    *(
        _text(_snake(counter), CloudSource(counter, CHANGE), enabled=False)
        for counter in (
            "pcsAvgOvpProtectCnt",
            "pcsOfpProtectCnt",
            "pcsOvpProtectCnt",
            "pcsUfpProtectCnt",
            "pcsUvp1ProtectCnt",
            "pcsUvp2ProtectCnt",
        )
    ),
    _text("cloud_error_count", CloudSource("errorCount", "ERROR_CHANGE_REPORT")),
    _cloud(
        "cloud_breaker_capacity",
        CloudSource("breakerCapacityMax", "EMS_PARAM_CHANGE_REPORT"),
        _sensor(
            "cloud_breaker_capacity",
            A,
            SensorDeviceClass.CURRENT,
            state_class=None,
            category=DIAG,
        ),
        enabled=False,
    ),
    _text("online", CloudSource("online")),
    _text("grid_state", CloudSource("sysGridSta"), enabled=False),
    _cloud(
        "charge_limit",
        CloudSource("sysBatChgUpLimit"),
        _sensor("charge_limit", PERCENTAGE, category=DIAG),
    ),
    _cloud(
        "other_pv_power",
        CloudSource("otherPvPwr", STREAM),
        _power("other_pv_power", DIAG),
        enabled=False,
    ),
    _cloud(
        "pv_inverter_power",
        CloudSource("pvInvPwr", STREAM),
        _power("pv_inverter_power", DIAG),
        enabled=False,
    ),
    *(
        _cloud(
            f"solar_{period}_cloud",
            CloudSource(f"{period}ElectricityGeneration"),
            _sensor(
                f"solar_{period}_cloud",
                UnitOfEnergy.KILO_WATT_HOUR,
                SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
            ),
        )
        for period in ("today", "month", "year", "total")
    ),
)


def _pack(
    key: str,
    cloud_key: str,
    unit: str | None = None,
    device_class: SensorDeviceClass | None = None,
    *,
    state_class: SensorStateClass | None = MEASUREMENT,
    category: EntityCategory | None = None,
    enabled: bool | None = None,
) -> CatalogueEntry:
    return _cloud(
        key,
        CloudSource(cloud_key, PACK),
        _sensor(key, unit, device_class, state_class=state_class, category=category),
        per_pack=True,
        enabled=enabled,
    )


_PACK_CLOUD: Final = (
    _pack("pack_soc", "bpSoc", PERCENTAGE, SensorDeviceClass.BATTERY),
    _pack(
        "pack_real_soc",
        "bpRealSoc",
        PERCENTAGE,
        SensorDeviceClass.BATTERY,
        enabled=False,
    ),
    _pack("pack_soh", "bpSoh", PERCENTAGE, category=DIAG),
    _pack("pack_real_soh", "bpRealSoh", PERCENTAGE, category=DIAG),
    _pack("pack_power", "bpPwr", W, SensorDeviceClass.POWER),
    _pack("pack_voltage", "bpVol", V, SensorDeviceClass.VOLTAGE, category=DIAG),
    _pack("pack_current", "bpAmp", A, SensorDeviceClass.CURRENT, category=DIAG),
    _pack(
        "pack_cell_max_voltage",
        "bpCellMaxVol",
        "mV",
        SensorDeviceClass.VOLTAGE,
        category=DIAG,
    ),
    _pack(
        "pack_cell_min_voltage",
        "bpCellMinVol",
        "mV",
        SensorDeviceClass.VOLTAGE,
        category=DIAG,
    ),
    *(
        _pack(
            f"pack_{_snake(name)[3:]}",
            name,
            UnitOfTemperature.CELSIUS,
            SensorDeviceClass.TEMPERATURE,
            category=DIAG,
        )
        for name in (
            "bpEnvTemp",
            "bpMaxCellTemp",
            "bpMinCellTemp",
            "bpMaxMosTemp",
            "bpMinMosTemp",
        )
    ),
    _pack(
        "pack_cycles",
        "bpCycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        category=DIAG,
    ),
    _pack(
        "pack_remaining",
        "bpRemainWatth",
        UnitOfEnergy.WATT_HOUR,
        SensorDeviceClass.ENERGY_STORAGE,
    ),
    # The packs' own counters, in Wh as the cloud reports them: cloud only, so
    # their source never changes.
    _pack(
        "pack_charged_total",
        "bpAccuChgEnergy",
        UnitOfEnergy.WATT_HOUR,
        SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    _pack(
        "pack_discharged_total",
        "bpAccuDsgEnergy",
        UnitOfEnergy.WATT_HOUR,
        SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    *(
        _text(key, CloudSource(name, PACK), per_pack=True)
        for key, name in (
            ("pack_state", "bpSysState"),
            ("pack_bms_run_state", "bmsRunSta"),
            ("pack_charge_state", "bmsChgDsgSta"),
            ("pack_module_state", "dabModSta"),
            ("pack_balance_state", "bpBalanceState"),
            ("pack_error_code", "bpErrCode"),
        )
    ),
    _cloud(
        "pack_update_time",
        CloudSource("bpTimestamp", PACK),
        _sensor(
            "pack_update_time",
            device_class=SensorDeviceClass.TIMESTAMP,
            state_class=None,
            category=DIAG,
        ),
        per_pack=True,
    ),
)

_SHARED_KEYS: Final = {key for key, _ in _JOINED} | {key for key, _ in _COUNTERS}

CATALOGUE_86: Final[tuple[CatalogueEntry, ...]] = (
    *(
        CatalogueEntry(key, "joined", _MODBUS_DEFS[key], modbus_key=key, cloud=source)
        for key, source in _JOINED
    ),
    *(
        CatalogueEntry(key, "counter", _MODBUS_DEFS[key], modbus_key=key, cloud=source)
        for key, source in _COUNTERS
    ),
    *(
        CatalogueEntry(key, "modbus", definition, modbus_key=key)
        for key, definition in _MODBUS_DEFS.items()
        if key not in _SHARED_KEYS
    ),
    *_INVERTER_CLOUD,
    *_PACK_CLOUD,
)
