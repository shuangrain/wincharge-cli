"""WinCharge Home Assistant 感測器 (Sensors)"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .wincharge_cli import parse_tou_map

if TYPE_CHECKING:
    from . import WinChargeDataUpdateCoordinator

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """設定 WinCharge 感測器實體。"""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: WinChargeDataUpdateCoordinator = data["coordinator"]
    config = data["config"]

    async_add_entities(
        [
            WinChargeStatusSensor(coordinator, config, entry.entry_id),
            WinChargeEnergySensor(coordinator, config, entry.entry_id),
            WinChargeFeeSensor(coordinator, config, entry.entry_id),
            WinChargeDurationSensor(coordinator, config, entry.entry_id),
        ]
    )


class WinChargeStatusSensor(CoordinatorEntity, SensorEntity):
    """WinCharge 主充電狀態監控感測器。"""

    def __init__(self, coordinator: WinChargeDataUpdateCoordinator, config: dict[str, Any], entry_id: str):
        super().__init__(coordinator)
        self._config = config
        self._attr_name = "WinCharge 充電樁狀態"
        self._attr_unique_id = f"wincharge_status_sensor_{entry_id}"
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data or {}
        if data.get("is_charging"):
            state_code = data.get("state_code", 2)
            state_map = {1: "準備中", 2: "充電中", 3: "已結束"}
            return state_map.get(state_code, f"充電中 (Code {state_code})")

        charger_info = data.get("charger_info", {})
        available = charger_info.get("available", True)
        if "available" in charger_info:
            return "待命 (充電樁可用)" if available else "待命 (充電樁使用中/告警)"
        return "待命"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        if data.get("is_charging"):
            res = data.get("transaction", {})
            raw_energy = float(res.get("energy", 0.0))
            energy_kwh = round(raw_energy / 1000.0, 3)
            tou_list = parse_tou_map(res.get("tou_price_power_map"))
            return {
                "order_id": data.get("order_id"),
                "charger": res.get("charger"),
                "connector": res.get("connector"),
                "started_timestamp": res.get("started"),
                "duration_seconds": res.get("duration"),
                "energy_kwh": energy_kwh,
                "energy_wh": raw_energy,
                "fee_ntd": res.get("fee"),
                "fee_of_unit": res.get("fee_of_unit"),
                "fee_description": res.get("fee_description"),
                "charging_discount": res.get("charging_discount"),
                "is_tou_charging": res.get("is_tou_charging"),
                "tou_breakdown": tou_list,
            }

        charger_info = data.get("charger_info", {})
        site_info = charger_info.get("site_info", {})
        charger_id = self._config.get("charger_id", "wincharge_ocppv16_SAMPLE123")
        return {
            "charger_id": charger_id,
            "available": charger_info.get("available", True),
            "site_name": site_info.get("name", "未知站點"),
            "rate": f"NT$ {site_info.get('rate', 0)}/{site_info.get('unit', '度')}",
            "last_order_id": data.get("order_id"),
        }


class WinChargeEnergySensor(CoordinatorEntity, SensorEntity):
    """已充電度數 (kWh) 感測器。"""

    def __init__(self, coordinator: WinChargeDataUpdateCoordinator, config: dict[str, Any], entry_id: str):
        super().__init__(coordinator)
        self._config = config
        self._attr_name = "WinCharge 已充電度數"
        self._attr_unique_id = f"wincharge_energy_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lightning-bolt-circle"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if not data.get("is_charging"):
            return 0.0
        res = data.get("transaction", {})
        raw_energy = float(res.get("energy", 0.0))
        return round(raw_energy / 1000.0, 3)


class WinChargeFeeSensor(CoordinatorEntity, SensorEntity):
    """當前充電費用 (NT$) 感測器。"""

    def __init__(self, coordinator: WinChargeDataUpdateCoordinator, config: dict[str, Any], entry_id: str):
        super().__init__(coordinator)
        self._config = config
        self._attr_name = "WinCharge 當前充電費用"
        self._attr_unique_id = f"wincharge_fee_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "NT$"
        self._attr_icon = "mdi:currency-usd"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        if not data.get("is_charging"):
            return 0.0
        res = data.get("transaction", {})
        return float(res.get("fee", 0.0))


class WinChargeDurationSensor(CoordinatorEntity, SensorEntity):
    """充電持續時間 (秒) 感測器。"""

    def __init__(self, coordinator: WinChargeDataUpdateCoordinator, config: dict[str, Any], entry_id: str):
        super().__init__(coordinator)
        self._config = config
        self._attr_name = "WinCharge 充電持續時間"
        self._attr_unique_id = f"wincharge_duration_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:timer-outline"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data or {}
        if not data.get("is_charging"):
            return 0
        res = data.get("transaction", {})
        return int(res.get("duration", 0))
