"""WinCharge Home Assistant 控制按鈕 (Buttons)"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .wincharge_cli import WinChargeClient, load_last_order, save_last_order

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """設定 WinCharge 按鈕實體。"""
    data = hass.data[DOMAIN][entry.entry_id]
    client: WinChargeClient = data["client"]
    config = data["config"]

    async_add_entities(
        [
            WinChargeStartButton(client, config, entry.entry_id),
            WinChargeStopButton(client, config, entry.entry_id),
        ]
    )


class WinChargeStartButton(ButtonEntity):
    """開啟充電控制按鈕。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "開始充電"
        self._attr_unique_id = f"wincharge_start_btn_{entry_id}"
        self._attr_icon = "mdi:play-circle-outline"

    def press(self) -> None:
        """點擊開啟充電。"""
        charger_id = self._config.get("charger_id", "wincharge_ocppv16_SAMPLE123")
        payment_password = self._config["payment_password"]

        try:
            account = self._client.get_account_info()
            phone = account.get("contact")
            card_id = self._client.get_primary_card_id(charger_id)
            invoice = self._client.get_invoice_setting()

            order_res = self._client.create_transaction_order(
                charger_id=charger_id,
                card_id=card_id,
                payment_password=payment_password,
            )
            order_id = order_res.get("order_id")
            if order_id:
                save_last_order(order_id)
                self._client.start_transaction(order_id=order_id, phone=phone, invoice_data=invoice)
                _LOGGER.info("成功發送開啟充電指令！Order ID: %s", order_id)
        except Exception as err:
            _LOGGER.error("開啟充電失敗: %s", err)


class WinChargeStopButton(ButtonEntity):
    """停止充電控制按鈕。"""

    def __init__(self, client: WinChargeClient, config: dict[str, Any], entry_id: str):
        self._client = client
        self._config = config
        self._attr_name = "停止充電"
        self._attr_unique_id = f"wincharge_stop_btn_{entry_id}"
        self._attr_icon = "mdi:stop-circle-outline"

    def press(self) -> None:
        """點擊停止充電。"""
        order_id = load_last_order()
        if not order_id:
            _LOGGER.error("無法停止：找不到活躍的 order_id 紀錄")
            return

        try:
            self._client.stop_transaction(order_id)
            _LOGGER.info("成功發送停止充電指令！Order ID: %s", order_id)
        except Exception as err:
            _LOGGER.error("停止充電失敗: %s", err)
