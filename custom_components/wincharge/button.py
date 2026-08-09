"""WinCharge Home Assistant 控制按鈕 (Buttons)"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .wincharge_cli import WinChargeClient, get_active_order_id, save_last_order

if TYPE_CHECKING:
    from . import WinChargeDataUpdateCoordinator

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
    coordinator: WinChargeDataUpdateCoordinator = data["coordinator"]
    config = data["config"]

    async_add_entities(
        [
            WinChargeStartButton(client, coordinator, config, entry.entry_id),
            WinChargeStopButton(client, coordinator, config, entry.entry_id),
            WinChargeRefreshButton(coordinator, config, entry.entry_id),
        ]
    )


class WinChargeStartButton(ButtonEntity):
    """開啟充電控制按鈕。"""

    def __init__(
        self,
        client: WinChargeClient,
        coordinator: WinChargeDataUpdateCoordinator,
        config: dict[str, Any],
        entry_id: str,
    ):
        self._client = client
        self._coordinator = coordinator
        self._config = config
        self._attr_name = "開始充電"
        self._attr_unique_id = f"wincharge_start_btn_{entry_id}"
        self._attr_icon = "mdi:play-circle-outline"

    def press(self) -> None:
        """點擊開啟充電。"""
        charger_id = self._config.get("charger_id", "wincharge_ocppv16_SAMPLE123")
        payment_password = self._config["payment_password"]

        # 防護 1：檢查最新訂單是否正處於充電中 (自動忽略超過 24 小時且 0kWh 的雲端殭屍訂單)
        last_order = get_active_order_id(self._client)
        if last_order:
            try:
                status_res = self._client.get_transaction_status(last_order)
                state = status_res.get("state")
                raw_energy = float(status_res.get("energy", 0.0))
                duration = int(status_res.get("duration", 0))

                if state == 2 and not (raw_energy == 0.0 and duration > 86400):
                    _LOGGER.warning(
                        "⚠️ 充電樁目前正處於充電狀態中 (Order ID: %s)，已自動攔截重複啟動請求！",
                        last_order,
                    )
                    return
                elif raw_energy == 0.0 and duration > 86400:
                    _LOGGER.warning(
                        "⚠️ 檢測到雲端殭屍訂單 [%s] (已卡住 %d 秒且度數為 0)，忽略並允許發起新充電！",
                        last_order,
                        duration,
                    )
            except Exception:
                pass

        try:
            # 防護 2：檢查充電樁即時可用狀態
            charger_info = self._client.get_charger_info(charger_id)
            if not charger_info.get("available", True):
                _LOGGER.warning("⚠️ 充電樁 [%s] 當前顯示為不可用 (告警或使用中)，自動中斷啟動請求！", charger_id)
                return

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
                self.hass.async_create_task(self._coordinator.async_request_refresh())
        except Exception as err:
            _LOGGER.error("開啟充電失敗: %s", err)


class WinChargeStopButton(ButtonEntity):
    """停止充電控制按鈕。"""

    def __init__(
        self,
        client: WinChargeClient,
        coordinator: WinChargeDataUpdateCoordinator,
        config: dict[str, Any],
        entry_id: str,
    ):
        self._client = client
        self._coordinator = coordinator
        self._config = config
        self._attr_name = "停止充電"
        self._attr_unique_id = f"wincharge_stop_btn_{entry_id}"
        self._attr_icon = "mdi:stop-circle-outline"

    def press(self) -> None:
        """點擊停止充電。"""
        order_id = get_active_order_id(self._client)
        if not order_id:
            _LOGGER.error("無法停止：找不到活躍的 order_id 紀錄")
            return

        # 防護：確認訂單處於充電中 (state == 2) 才允許停止
        try:
            status_res = self._client.get_transaction_status(order_id)
            state = status_res.get("state")
            raw_energy = float(status_res.get("energy", 0.0))
            duration = int(status_res.get("duration", 0))

            if state != 2 and not (raw_energy == 0.0 and duration > 86400):
                _LOGGER.warning(
                    "⚠️ 訂單 (Order ID: %s) 當前非充電狀態 (Code: %s)，已自動攔截停止充電請求！",
                    order_id,
                    state,
                )
                return
        except Exception:
            pass

        try:
            self._client.stop_transaction(order_id)
            _LOGGER.info("成功發送停止充電指令！Order ID: %s", order_id)
            self.hass.async_create_task(self._coordinator.async_request_refresh())
        except Exception as err:
            _LOGGER.error("停止充電失敗: %s", err)
            # 若遇到 status 36 (ERROR_STOP_TRANSACTION)，代表該訂單在伺服器端已無法停止，自動解開卡住狀態
            if "status: 36" in str(err) or "ERROR_STOP_TRANSACTION" in str(err):
                _LOGGER.warning("⚠️ 訂單 [%s] 在伺服器端已無法停止 (status 36)，自動重置卡住狀態", order_id)
                self.hass.async_create_task(self._coordinator.async_request_refresh())


class WinChargeRefreshButton(ButtonEntity):
    """手動即時重新整理數據控制按鈕。"""

    def __init__(self, coordinator: WinChargeDataUpdateCoordinator, config: dict[str, Any], entry_id: str):
        self._coordinator = coordinator
        self._config = config
        self._attr_name = "重新整理數據"
        self._attr_unique_id = f"wincharge_refresh_btn_{entry_id}"
        self._attr_icon = "mdi:refresh"

    async def async_press(self) -> None:
        """點擊手動即時發送 API 請求抓取最新數據。"""
        _LOGGER.info("🔄 [WinCharge] 使用者點擊【重新整理數據】按鈕，強制發起即時更新...")
        await self._coordinator.async_request_refresh()
