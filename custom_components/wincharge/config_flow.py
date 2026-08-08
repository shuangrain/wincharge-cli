"""Home Assistant UI 設定流程 (Config Flow)"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .wincharge_cli import DEFAULT_API_KEY, WinChargeClient

DOMAIN = "wincharge"
_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("member_id"): str,
        vol.Required("password_hash"): str,
        vol.Required("payment_password"): str,
        vol.Optional("charger_id", default="wincharge_ocppv16_SAMPLE123"): str,
        vol.Optional("api_key", default=DEFAULT_API_KEY): str,
    }
)


class WinChargeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """處理 GUI 設定精靈。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """處理使用者初始化新增介面 (輸入帳號與密碼雜湊自動驗證)。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            pwd = user_input.get("password_hash") or user_input.get("password")
            try:
                # 測試帳號密碼登入
                await self.hass.async_add_executor_job(
                    WinChargeClient.login,
                    user_input["member_id"],
                    pwd,
                    user_input.get("api_key", DEFAULT_API_KEY),
                )

                return self.async_create_entry(
                    title=f"WinCharge ({user_input['charger_id']})",
                    data=user_input,
                )
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("WinCharge 帳號登入驗證失敗: %s", err)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """處理重新設定介面 (用於更新帳號、密碼雜湊或充電樁 ID)。"""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            pwd = user_input.get("password_hash") or user_input.get("password")
            try:
                # 測試帳號密碼登入
                await self.hass.async_add_executor_job(
                    WinChargeClient.login,
                    user_input["member_id"],
                    pwd,
                    user_input.get("api_key", DEFAULT_API_KEY),
                )

                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, **user_input},
                )
            except Exception as err:
                errors["base"] = "cannot_connect"
                _LOGGER.error("WinCharge 重新設定驗證失敗: %s", err)

        default_pwd = entry.data.get("password_hash") or entry.data.get("password", "")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required("member_id", default=entry.data.get("member_id", "")): str,
                    vol.Required("password_hash", default=default_pwd): str,
                    vol.Required("payment_password", default=entry.data.get("payment_password", "")): str,
                    vol.Optional(
                        "charger_id", default=entry.data.get("charger_id", "wincharge_ocppv16_SAMPLE123")
                    ): str,
                    vol.Optional("api_key", default=entry.data.get("api_key", DEFAULT_API_KEY)): str,
                }
            ),
            errors=errors,
        )
