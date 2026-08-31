"""WinCharge Home Assistant Custom Integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

try:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
except ImportError:
    # 當以獨立 CLI 模式運行時 (無 homeassistant 套件)，建立虛擬類別以避免頂層載入失敗
    class DataUpdateCoordinator:  # type: ignore[no-redef]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __class_getitem__(cls, item: Any) -> type:
            return cls

    class UpdateFailed(Exception):  # type: ignore[no-redef]
        pass


from .wincharge_cli import DEFAULT_API_KEY, WinChargeClient, get_active_order_id

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)


class WinChargeDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """WinCharge 雙重動態輪詢數據協調器 (待命與充電中雙重週期動態切換)。"""

    def __init__(
        self,
        hass: HomeAssistant,
        client: WinChargeClient,
        config: dict[str, Any],
    ):
        self.client = client
        self.config = config

        idle_sec = int(config.get("idle_interval", 60))
        charging_sec = int(config.get("charging_interval", 10))

        self.idle_interval = timedelta(seconds=idle_sec)
        self.charging_interval = timedelta(seconds=charging_sec)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=self.idle_interval,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """定時抓取數據，並依狀態動態切換輪詢頻率。"""
        try:
            return await self.hass.async_add_executor_job(self._fetch_data)
        except Exception as err:
            raise UpdateFailed(f"更新 WinCharge 數據失敗: {err}") from err

    def _fetch_data(self) -> dict[str, Any]:
        charger_id = self.config.get("charger_id", "wincharge_ocppv16_SAMPLE123")
        order_id = get_active_order_id(self.client)
        _LOGGER.debug("🐛 [WinCharge Coordinator] 開始輪詢資料 (Charger: %s, Order ID: %s)...", charger_id, order_id)

        is_charging = False
        data: dict[str, Any] = {"is_charging": False, "order_id": order_id}

        if order_id:
            try:
                res = self.client.get_transaction_status(order_id)
                state_code = res.get("state")
                raw_energy = float(res.get("energy", 0.0))
                duration = int(res.get("duration", 0))

                if state_code in (1, 2) and not (raw_energy == 0.0 and duration > 86400):
                    is_charging = True
                    data["is_charging"] = True
                    data["state_code"] = state_code
                    data["transaction"] = res
            except Exception as err:
                _LOGGER.warning("查詢訂單 [%s] 充電狀態失敗: %s", order_id, err)

        # 雙重動態輪詢頻率切換
        target_interval = self.charging_interval if is_charging else self.idle_interval
        if self.update_interval != target_interval:
            self.update_interval = target_interval
            _LOGGER.info(
                "⚡ [WinCharge] 雙重動態輪詢切換為 %d 秒模式 (%s)",
                int(target_interval.total_seconds()),
                "⚡ 充電中高頻輪詢" if is_charging else "🟢 待命省流量輪詢",
            )

        if not is_charging:
            try:
                charger_info = self.client.get_charger_info(charger_id)
                data["charger_info"] = charger_info
            except Exception as err:
                _LOGGER.warning("查詢充電樁 [%s] 即時資訊失敗: %s", charger_id, err)

        _LOGGER.debug("🐛 [WinCharge Coordinator] 輪詢完成，最新整合狀態數據: %s", data)
        return data


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """從 GUI 設定流程載入 WinCharge 整合。"""
    from homeassistant.const import Platform

    platforms: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

    hass.data.setdefault(DOMAIN, {})

    config = entry.data
    api_key = config.get("api_key", DEFAULT_API_KEY)
    api_token = config.get("api_token")
    api_uid = config.get("api_uid")
    member_id = config.get("member_id")
    password = config.get("password_hash") or config.get("password")
    refresh_hours = config.get("refresh_hours", 24)

    def create_client():
        return WinChargeClient(
            api_key=api_key,
            api_token=api_token,
            api_uid=api_uid,
            member_id=member_id,
            password=password,
            refresh_hours=refresh_hours,
        )

    client = await hass.async_add_executor_job(create_client)
    coordinator = WinChargeDataUpdateCoordinator(hass, client, config)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "config": config,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """解除載入 WinCharge 整合。"""
    from homeassistant.const import Platform

    platforms: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
