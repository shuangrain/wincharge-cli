"""WinCharge Home Assistant 感測器 (Sensors)"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .wincharge_cli import WinChargeClient, load_last_order

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """設定 WinCharge 感測器實體。"""
    data = hass.data[DOMAIN][entry.entry_id]
    client: WinChargeClient = data["client"]
    config = data["config"]

    async_add_entities(
        [
            WinChargeStatusSensor(client, config, entry.entry_id),
            WinChargeEnergySensor(client, config, entry.entry_id),
            WinChargeFeeSensor(client, config, entry.entry_id),
            WinChargeDurationSensor(client, config, entry.entry_id),
        ],
        update_before_add=True,
    )


class WinChargeStatusSensor(SensorEntity):
    """WinCharge 主充電狀態監控感測器。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "WinCharge 充電樁狀態"
        self._attr_unique_id = f"wincharge_status_sensor_{entry_id}"
        self._attr_icon = "mdi:ev-station"
        self._state = "待命"
        self._attributes: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes

    def update(self) -> None:
        """更新狀態感測器與完整屬性。"""
        charger_id = self._config.get("charger_id", "wincharge_ocppv16_SAMPLE123")
        order_id = load_last_order()

        if order_id:
            try:
                res = self._client.get_transaction_status(order_id)
                state_code = res.get("state")
                state_map = {1: "準備中", 2: "充電中", 3: "已結束"}

                if state_code in (1, 2):
                    self._state = state_map.get(state_code, f"充電中 (Code {state_code})")
                    self._attributes = {
                        "order_id": order_id,
                        "charger": res.get("charger"),
                        "connector": res.get("connector"),
                        "started_timestamp": res.get("started"),
                        "duration_seconds": res.get("duration"),
                        "energy_kwh": res.get("energy"),
                        "fee_ntd": res.get("fee"),
                        "fee_of_unit": res.get("fee_of_unit"),
                        "fee_description": res.get("fee_description"),
                        "charging_discount": res.get("charging_discount"),
                        "is_tou_charging": res.get("is_tou_charging"),
                    }
                    return
            except Exception as err:
                _LOGGER.warning("查詢訂單 [%s] 充電進度失敗: %s", order_id, err)

        # 非充電狀態：查詢充電樁實體即時狀態與費率
        try:
            charger_info = self._client.get_charger_info(charger_id)
            site_info = charger_info.get("site_info", {})
            available = charger_info.get("available", True)

            self._state = "待命 (充電樁可用)" if available else "待命 (充電樁使用中/告警)"
            self._attributes = {
                "charger_id": charger_id,
                "available": available,
                "site_name": site_info.get("name", "未知站點"),
                "rate": f"NT$ {site_info.get('rate')}/{site_info.get('unit')}",
                "last_order_id": order_id,
            }
        except Exception as err:
            _LOGGER.warning("查詢充電樁 [%s] 即時狀態失敗: %s", charger_id, err)
            self._state = "待命"
            self._attributes = {"last_order_id": order_id}


class WinChargeEnergySensor(SensorEntity):
    """已充電度數 (kWh) 感測器。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "WinCharge 已充電度數"
        self._attr_unique_id = f"wincharge_energy_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:lightning-bolt-circle"
        self._state: float | None = 0.0

    @property
    def native_value(self) -> float | None:
        return self._state

    def update(self) -> None:
        """更新已充電度數。"""
        order_id = load_last_order()
        if not order_id:
            self._state = 0.0
            return
        try:
            res = self._client.get_transaction_status(order_id)
            self._state = float(res.get("energy", 0.0))
        except Exception:
            pass


class WinChargeFeeSensor(SensorEntity):
    """當前充電費用 (NT$) 感測器。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "WinCharge 當前充電費用"
        self._attr_unique_id = f"wincharge_fee_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "NT$"
        self._attr_icon = "mdi:currency-usd"
        self._state: float | None = 0.0

    @property
    def native_value(self) -> float | None:
        return self._state

    def update(self) -> None:
        """更新當前費用。"""
        order_id = load_last_order()
        if not order_id:
            self._state = 0.0
            return
        try:
            res = self._client.get_transaction_status(order_id)
            self._state = float(res.get("fee", 0.0))
        except Exception:
            pass


class WinChargeDurationSensor(SensorEntity):
    """充電持續時間 (秒) 感測器。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "WinCharge 充電持續時間"
        self._attr_unique_id = f"wincharge_duration_sensor_{entry_id}"
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_icon = "mdi:timer-outline"
        self._state: int | None = 0

    @property
    def native_value(self) -> int | None:
        return self._state

    def update(self) -> None:
        """更新充電持續時間。"""
        order_id = load_last_order()
        if not order_id:
            self._state = 0
            return
        try:
            res = self._client.get_transaction_status(order_id)
            self._state = int(res.get("duration", 0))
        except Exception:
            pass
