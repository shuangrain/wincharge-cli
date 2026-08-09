"""WinCharge Home Assistant Custom Integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .wincharge_cli import DEFAULT_API_KEY, WinChargeClient

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)


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

    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "config": config,
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
