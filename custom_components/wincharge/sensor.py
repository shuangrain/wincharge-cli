"""WinCharge Home Assistant 感測器 (Sensors)"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from wincharge_cli import WinChargeClient, load_last_order

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

    async_add_entities([WinChargeStatusSensor(client, config, entry.entry_id)], update_before_add=True)


class WinChargeStatusSensor(SensorEntity):
    """WinCharge 充電狀態監控感測器。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "WinCharge 充電樁狀態"
        self._attr_unique_id = f"wincharge_sensor_{entry_id}"
        self._attr_icon = "mdi:ev-station"
        self._state = "未充電"
        self._attributes: dict[str, Any] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attributes

    def update(self) -> None:
        """更新感測器數值。"""
        order_id = load_last_order()
        if not order_id:
            self._state = "待命 (無活躍訂單)"
            self._attributes = {}
            return

        try:
            res = self._client.get_transaction_status(order_id)
            state_map = {1: "準備中", 2: "充電中", 3: "已結束"}
            self._state = state_map.get(res.get("state"), f"狀態 Code {res.get('state')}")
            self._attributes = {
                "order_id": order_id,
                "charger": res.get("charger"),
                "connector": res.get("connector"),
                "energy_kwh": res.get("energy"),
                "fee_ntd": res.get("fee"),
                "duration_seconds": res.get("duration"),
                "fee_description": res.get("fee_description"),
            }
        except Exception as err:
            _LOGGER.warning("無法取得充電狀態: %s", err)
