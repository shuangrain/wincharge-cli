# /// script
# dependencies = [
#   "requests>=2.28.0",
# ]
# ///

"""WinCharge 充電樁 CLI 控制工具 (PEP 723) 與 HACS 核心模組

⚠️ 免責聲明 (Disclaimer):
    本工具僅供個人技術測試、研究與學習使用。使用本工具進行任何 API 呼叫、充電作業衍生之費用、
    設備損害或法律責任，開發者不負任何形式之責任。請確保在合法與授權環境下使用。
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://new-home.wincharge.net"
DEFAULT_API_KEY = "IUOXLJtNtAk5z0CWV8xwexTns6LG3eRN"
LAST_ORDER_FILE = Path.home() / ".wincharge_last_order"
_LOGGER = logging.getLogger(__name__)


def format_password_hash(password: str) -> str:
    """傳入的 32 位 MD5 小寫 Hex 密碼雜湊值 (腳本不另外進行 MD5 雜湊處理)"""
    return str(password).strip().lower()


# 錯誤訊息對照字典
ERROR_TRANSLATION_MAP = {
    "ERROR_CHARGER_IN_USER": "充電樁目前正由其他使用者佔用或正在充電中",
    "ERROR_CHARGER_OFFLINE": "充電樁目前處於離線狀態，無法對外通訊",
    "ERROR_PAYMENT_PASSWORD": "交易密碼驗證失敗，請確認密碼是否正確",
    "ERROR_CARD_INVALID": "指定的支付卡片無效或已被停用",
    "ERROR_NO_CARD": "帳號內未繫結有效的支付卡片",
    "ERROR_UNAUTHORIZED": "認證標頭失效或 Token 已過期",
}

STATUS_TRANSLATION_MAP = {
    24: "API Token 已過期 (ERROR_TOKEN_EXPIRED)",
    25: "API Token 無效或未找到 (ERROR_TOKEN_NOT_FOUND)",
    64: "交易密碼驗證失敗，請確認輸入的交易密碼 (payment_password) 是否正確",
    17: "充電樁目前正由其他使用者佔用或正在充電中",
    18: "充電槍已拔除或充電樁斷線 (ERROR_CHARGER_DISCONNECTED)",
    36: "此充電訂單已無法透過 API 停止或實體槍已拔除 (ERROR_STOP_TRANSACTION)",
}


def save_last_order(order_id: str) -> None:
    """將最新的 order_id 寫入本機快取檔案"""
    try:
        LAST_ORDER_FILE.write_text(order_id.strip(), encoding="utf-8")
    except Exception:
        pass


def load_last_order() -> str | None:
    """從本機快取檔案讀取最新的 order_id"""
    try:
        if LAST_ORDER_FILE.exists():
            content = LAST_ORDER_FILE.read_text(encoding="utf-8").strip()
            return content if content else None
    except Exception:
        pass
    return None


def translate_error(error_msg: str, status: int | None = None) -> str:
    """將 API 錯誤代碼翻譯為易懂的中文訊息"""
    msg = str(error_msg).strip()
    explanation = ERROR_TRANSLATION_MAP.get(msg)
    if not explanation and status is not None:
        explanation = STATUS_TRANSLATION_MAP.get(status)

    status_str = f" (status: {status})" if status is not None else ""

    if explanation:
        return f"{explanation} [{msg}]{status_str}"
    return f"{msg}{status_str}"


def parse_tou_map(tou_data: Any) -> list[dict[str, Any]]:
    """解析時間電價分時統計字典 (tou_price_power_map)"""
    if not isinstance(tou_data, dict):
        return []

    result = []
    for rate_key, item in tou_data.items():
        if isinstance(item, dict):
            price = float(item.get("price", rate_key))
            acc_power = float(item.get("acc_power", 0.0))
            acc_fee = float(item.get("acc_fee", 0.0))
            result.append(
                {
                    "rate_ntd": price,
                    "kwh": acc_power,
                    "fee_ntd": acc_fee,
                }
            )
    return result


def validate_api_token(token: str) -> dict[str, Any]:
    """解碼並驗證 API Token (JWT) 的有效性"""
    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("API Token 格式無效：必須為包含 3 個部分的標準 JWT")

    payload_b64 = parts[1]
    missing_padding = len(payload_b64) % 4
    if missing_padding:
        payload_b64 += "=" * (4 - missing_padding)

    try:
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"API Token 解碼失敗: {e}") from e

    # 1. 驗證發行者 (iss)
    iss = payload.get("iss")
    if iss != "wincharge.com":
        raise ValueError(f"API Token 驗證失敗：iss 應為 'wincharge.com'，實際為 '{iss}'")

    # 2. 驗證過期時間 (exp)
    exp = payload.get("exp")
    if exp is not None:
        now = time.time()
        if now > exp:
            exp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))
            raise ValueError(f"API Token 驗證失敗：Token 已過期 (過期時間: {exp_str})")

    # 3. 驗證權限 (perms)
    perms = payload.get("perms", [])
    if "PERM_CHARGE_USER" not in perms:
        raise ValueError(f"API Token 驗證失敗：缺少必要權限 'PERM_CHARGE_USER' (當前權限: {perms})")

    return payload


def get_jwt_exp_time_str(token: str) -> str | None:
    """從 JWT Token 中解碼並格式化過期時間字串 (exp)"""
    try:
        payload = validate_api_token(token)
        exp = payload.get("exp")
        if exp is not None:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))
    except Exception:
        pass
    return None


class WinChargeClient:
    def __init__(
        self,
        api_key: str = DEFAULT_API_KEY,
        api_token: str | None = None,
        api_uid: str | None = None,
        member_id: str | None = None,
        password: str | None = None,
        debug: bool = False,
        refresh_hours: int = 24,
    ):
        self.api_key = api_key or DEFAULT_API_KEY
        self.member_id = member_id
        self.password = password
        self.debug = debug
        self.refresh_seconds = refresh_hours * 3600
        self.last_login_time = time.time() if api_token else 0.0

        self.api_token = api_token
        self.api_uid = api_uid

        # 若提供 member_id 與 password 且無初始 token，立即執行登入
        if self.member_id and self.password and not self.api_token:
            login_info = self.login(
                member_id=self.member_id,
                password=self.password,
                api_key=self.api_key,
                debug=self.debug,
            )
            self.api_token = login_info["api_token"]
            self.api_uid = login_info["api_uid"]
            self.last_login_time = time.time()

        self.jwt_payload: dict[str, Any] = {}
        if self.api_token:
            try:
                self.jwt_payload = validate_api_token(self.api_token)
            except Exception:
                pass

        self.session = requests.Session()
        self._update_session_headers()

    def _update_session_headers(self) -> None:
        """更新 HTTP Session 中的認證標頭"""
        self.session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "cache-control": "no-cache",
                "pragma": "no-cache",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
                ),
                "x-api-key": self.api_key,
                "x-api-token": self.api_token or "",
                "x-api-uid": self.api_uid or "",
            }
        )
        self.session.cookies.set("i18n_redirected", "zh-TW")

    @classmethod
    def login(
        cls,
        member_id: str,
        password: str,
        api_key: str = DEFAULT_API_KEY,
        debug: bool = False,
    ) -> dict[str, Any]:
        """呼叫 POST /api/account/login 進行帳號密碼登入 (自動做 MD5 雜湊)"""
        url = f"{BASE_URL}/api/account/login"
        pwd_hash = format_password_hash(password)

        _LOGGER.info("🔑 [WinCharge] 開始發送 POST /api/account/login 登入請求 (Member ID: %s)...", member_id)

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json;charset=UTF-8",
            "origin": BASE_URL,
            "pragma": "no-cache",
            "referer": f"{BASE_URL}/user/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
            ),
            "x-api-key": api_key,
        }
        payload = {
            "member_id": str(member_id).strip(),
            "password": pwd_hash,
            "password_repeat": pwd_hash,
        }

        _LOGGER.debug(
            "🐛 [WinCharge Login Request] POST %s\nHeaders: %s\nPayload: %s",
            url,
            headers,
            payload,
        )

        if debug:
            print("\n" + "=" * 60)
            print(f"🐛 [DEBUG LOGIN REQUEST] POST {url}")
            print(f"▸ Member ID: {member_id}")
            print(f"▸ Payload:\n{json.dumps(payload, indent=2)}")
            print("=" * 60)

        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except Exception as http_err:
            _LOGGER.error("❌ [WinCharge] 登入 HTTP 請求失敗 (Member ID: %s): %s", member_id, http_err)
            raise http_err

        _LOGGER.debug(
            "🐛 [WinCharge Login Response] Status %s\nHeaders: %s\nBody: %s",
            response.status_code,
            dict(response.headers),
            response.text,
        )

        if debug:
            print(f"🐛 [DEBUG LOGIN RESPONSE] Status: {response.status_code}")
            print("▸ Response Headers:", dict(response.headers))
            print("▸ Response Body:", response.text)

        data = response.json()
        if data.get("status") != 0 and data.get("code") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            _LOGGER.error("❌ [WinCharge] 帳號登入失敗 (Member ID: %s): %s", member_id, err)
            raise RuntimeError(f"帳號登入失敗: {err}")

        token = (
            data.get("token")
            or response.headers.get("x-api-token")
            or response.headers.get("X-Api-Token")
            or data.get("api_token")
        )
        uid = (
            data.get("member_id")
            or response.headers.get("x-api-uid")
            or response.headers.get("X-Api-Uid")
            or data.get("api_uid")
            or member_id
        )

        if not token:
            _LOGGER.error("❌ [WinCharge] 登入失敗：伺服器回應未包含有效的 x-api-token 憑證標頭")
            raise RuntimeError("登入失敗：伺服器回應中未包含有效的 x-api-token 憑證標頭")

        exp_str = get_jwt_exp_time_str(token)
        exp_info = f", 原廠 JWT 效期至: {exp_str}" if exp_str else ""
        _LOGGER.info("✅ [WinCharge] 帳號登入成功！(UID: %s, Member ID: %s%s)", uid, member_id, exp_info)

        return {
            "api_key": api_key,
            "api_token": token,
            "api_uid": uid,
            "raw_response": data,
        }

    def _force_relogin(self) -> None:
        """強制重新登入取得全新 Token 並更新 Session"""
        if self.member_id and self.password:
            login_info = self.login(
                member_id=self.member_id,
                password=self.password,
                api_key=self.api_key,
                debug=self.debug,
            )
            self.api_token = login_info["api_token"]
            self.api_uid = login_info["api_uid"]
            self.last_login_time = time.time()
            self._update_session_headers()
            exp_str = get_jwt_exp_time_str(self.api_token)
            exp_info = f", 原廠 JWT 效期至: {exp_str}" if exp_str else ""
            _LOGGER.info("✅ [WinCharge] 強制重新登入成功，已換發最新 Token (UID: %s%s)", self.api_uid, exp_info)

    def _is_token_expired_response(self, response: requests.Response) -> bool:
        """檢查 API 回應是否為 Token 過期或未找到 (HTTP 401 或 status 24/25)"""
        if response.status_code == 401:
            return True
        try:
            data = response.json()
            if data.get("status") in (24, 25) or "ERROR_TOKEN" in str(data.get("error_msg", "")):
                return True
        except Exception:
            pass
        return False

    def _ensure_valid_token(self) -> None:
        """檢查當前 Token 是否已超過自訂快取時間；若是且提供有帳號密碼，則自動重新登入續約 Token！"""
        now = time.time()
        elapsed = now - self.last_login_time

        # 已有 Token 且未滿快取設定時間
        if self.api_token and self.api_uid and (elapsed < self.refresh_seconds):
            return

        if self.member_id and self.password:
            hours_passed = round(elapsed / 3600.0, 1) if self.last_login_time else 0
            refresh_h = round(self.refresh_seconds / 3600.0, 1)
            _LOGGER.info(
                "🔑 [WinCharge] 距離上次登入已過 %.1f 小時 (快取設定週期: %.1f 小時)，執行自動登入取得最新 API Token (Member ID: %s)...",
                hours_passed,
                refresh_h,
                self.member_id,
            )
            try:
                self._force_relogin()
            except Exception as e:
                _LOGGER.error("❌ [WinCharge] 自動登入失敗: %s", e)
                if not self.api_token:
                    raise e

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """發送 HTTP 請求 (自動在發送前進行 Token 有效性檢查與續約，並支援 Token 失效自動重登重試)"""
        self._ensure_valid_token()

        merged_headers = {**self.session.headers, **kwargs.get("headers", {})}

        _LOGGER.debug(
            "🐛 [WinCharge HTTP Request] %s %s\nHeaders: %s\nPayload: %s",
            method.upper(),
            url,
            merged_headers,
            kwargs.get("json") or kwargs.get("data") or "",
        )

        if self.debug:
            print("\n" + "=" * 60)
            print(f"🐛 [DEBUG REQUEST] HTTP {method.upper()} {url}")
            print("▸ Headers:")
            for k, v in merged_headers.items():
                print(f"    {k}: {v}")
            if "json" in kwargs:
                print("▸ JSON Payload:")
                print(json.dumps(kwargs["json"], indent=2, ensure_ascii=False))
            elif "data" in kwargs:
                print(f"▸ Data Payload:\n  {kwargs['data']}")
            print("-" * 60)

        response = self.session.request(method, url, **kwargs)

        _LOGGER.debug(
            "🐛 [WinCharge HTTP Response] %s %s -> Status %s %s\nHeaders: %s\nBody: %s",
            method.upper(),
            url,
            response.status_code,
            response.reason,
            dict(response.headers),
            response.text[:2000] if len(response.text) > 2000 else response.text,
        )

        if self.debug:
            print(f"🐛 [DEBUG RESPONSE] Status: {response.status_code} {response.reason}")
            print("▸ Response Headers:")
            for k, v in response.headers.items():
                print(f"    {k}: {v}")
            print("▸ Response Body:")
            try:
                print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                print(response.text)
            print("=" * 60 + "\n")

        # 自動自癒：若遇到 Token 過期/未找到 (status 24/25 或 HTTP 401)，自動重新登入並重試該請求！
        if self._is_token_expired_response(response):
            if self.member_id and self.password:
                _LOGGER.warning(
                    "⚠️ [WinCharge] 伺服器回傳 Token 無效或已過期 (status: 25)，自動強制重新登入並重試請求..."
                )
                self._force_relogin()
                # 重新帶入最新標頭重試一次
                kwargs["headers"] = {**kwargs.get("headers", {}), **self.session.headers}
                response = self.session.request(method, url, **kwargs)
                _LOGGER.debug(
                    "🐛 [WinCharge HTTP Retry Response] %s %s -> Status %s, Body: %s",
                    method.upper(),
                    url,
                    response.status_code,
                    response.text,
                )

        return response

    def get_account_info(self) -> dict[str, Any]:
        """Step 1: 取得帳號資訊並驗證 contact 欄位"""
        url = f"{BASE_URL}/api/account"
        headers = {"referer": f"{BASE_URL}/user/account/settings"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"帳號資訊取得失敗: {err}")

        contact = data.get("contact", "")
        if not contact or not str(contact).strip():
            raise ValueError("帳號驗證失敗：聯絡電話/資訊 (contact) 為空")

        return data

    def get_primary_card_id(self, charger_id: str) -> str:
        """Step 2: 取得卡片清單，確認有卡片並回傳預設卡片 ID"""
        url = f"{BASE_URL}/api/account/cards"
        headers = {"referer": f"{BASE_URL}/charger/{charger_id}"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"卡片列表取得失敗: {err}")

        cards = data.get("cards", [])
        if not cards or len(cards) == 0:
            raise ValueError("卡片驗證失敗：帳號內沒有已綁定的支付卡片 (cards 長度為 0)")

        primary_card = next((c for c in cards if c.get("primary") is True), cards[0])
        card_id = primary_card.get("id")
        if not card_id:
            raise ValueError("無效的卡片資料：找不到卡片 ID")

        return card_id

    def get_invoice_setting(self) -> dict[str, Any]:
        """Step 3: 取得發票設定資訊"""
        url = f"{BASE_URL}/api/account/invoice"
        headers = {"referer": f"{BASE_URL}/user/account/settings"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"發票設定取得失敗: {err}")

        invoice = data.get("invoice")
        if not invoice or not isinstance(invoice, dict):
            raise ValueError("發票設定驗證失敗：未取得有效的 invoice 物件")

        return invoice

    def get_charger_info(self, charger_id: str, connector: str = "") -> dict[str, Any]:
        """Step 4: 查詢充電樁即時狀態與站點資訊"""
        url = f"{BASE_URL}/api/chargers/{charger_id}?connector={connector}"
        headers = {"referer": f"{BASE_URL}/charger/{charger_id}"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 0 and data.get("status") not in (0, None):
            raise RuntimeError(f"充電樁資訊查詢失敗: code={data.get('code')}")

        return data

    def create_transaction_order(
        self, charger_id: str, card_id: str, payment_password: str, connector: str = ""
    ) -> dict[str, Any]:
        """Step 5: 建立交易訂單，取得 order_id"""
        url = f"{BASE_URL}/api/chargers/{charger_id}/transactions?connector={connector}"
        headers = {
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/charger/{charger_id}",
            "content-type": "application/json;charset=UTF-8",
        }
        payload = {
            "payment": 2,
            "card_id": card_id,
            "payment_password": payment_password,
        }

        response = self._request("POST", url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"建立充電訂單失敗: {err}")

        return data

    def start_transaction(self, order_id: str, phone: str, invoice_data: dict[str, Any]) -> dict[str, Any]:
        """Step 6: 對 order_id 發送 PUT /start 正式開啟充電"""
        url = f"{BASE_URL}/api/transactions/{order_id}/start"
        headers = {
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/transaction/{order_id}",
            "content-type": "application/json;charset=UTF-8",
        }
        payload = {
            "phone": str(phone).strip(),
            "invoice": invoice_data,
        }

        response = self._request("PUT", url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"發送啟動充電指令失敗: {err}")

        return data

    def get_transaction_status(self, order_id: str) -> dict[str, Any]:
        """查詢交易/充電狀態"""
        url = f"{BASE_URL}/api/transactions/{order_id}"
        headers = {"referer": f"{BASE_URL}/transaction/{order_id}"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        return response.json()

    def stop_transaction(self, order_id: str) -> dict[str, Any]:
        """停止充電交易"""
        url = f"{BASE_URL}/api/transactions/{order_id}/stop"
        headers = {
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/transaction/{order_id}",
            "content-length": "0",
        }

        response = self._request("PUT", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            err = translate_error(data.get("error_msg", "未知錯誤"), data.get("status"))
            raise RuntimeError(f"停止充電失敗: {err}")

        return data

    # -------------------------------------------------------------------------
    # 新增：帳號活躍與歷史交易 API
    # -------------------------------------------------------------------------

    def get_active_transactions(self) -> list[dict[str, Any]]:
        """查詢目前帳號下正處於充電/活躍狀態的交易紀錄 (show_charging_only=1)"""
        url = f"{BASE_URL}/api/account/transactions?show_charging_only=1"
        headers = {"referer": f"{BASE_URL}/user/transactions"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("transactions", data.get("list", data.get("data", [])))
        return []

    def get_transaction_history(self, page: int = 1, page_count: int = 10) -> dict[str, Any]:
        """查詢帳號歷史充電交易紀錄 (分頁)"""
        url = f"{BASE_URL}/api/account/transactions?page={page}&page_count={page_count}"
        headers = {"referer": f"{BASE_URL}/user/transactions"}

        response = self._request("GET", url, headers=headers)
        response.raise_for_status()
        return response.json()


def get_active_order_id(client: WinChargeClient | None = None) -> str | None:
    """動態獲取當前活躍訂單 ID：優先從線上 API 查詢 (自動過濾過期殭屍訂單)，找不到再從本機快取檔讀取"""
    if client:
        try:
            active_list = client.get_active_transactions()
            if active_list and len(active_list) > 0:
                valid_orders = []
                for item in active_list:
                    raw_energy = float(item.get("energy", 0.0))
                    duration = int(item.get("duration", 0))
                    # 若充電度數為 0 且持續時間超過 24 小時 (86400 秒)，判定為 WinCharge 伺服器殭屍訂單進行過濾
                    if raw_energy == 0.0 and duration > 86400:
                        _LOGGER.warning(
                            "⚠️ [WinCharge] 自動過濾雲端殭屍訂單 [%s] (已卡住 %d 秒且充電度數為 0)",
                            item.get("order_id"),
                            duration,
                        )
                        continue
                    valid_orders.append(item)

                if valid_orders:
                    first_order = valid_orders[0]
                    order_id = first_order.get("order_id") or first_order.get("id")
                    if order_id:
                        save_last_order(str(order_id))
                        return str(order_id)
        except Exception:
            pass

    return load_last_order()


# -------------------------------------------------------------------------
# Subcommand 處理邏輯
# -------------------------------------------------------------------------


def handle_start(client: WinChargeClient, args: argparse.Namespace):
    """處理開啟充電完整流程"""
    charger_id = args.charger_id
    connector = args.connector or ""
    payment_password = args.payment_password

    if not payment_password:
        print(
            "❌ 錯誤: 開始充電需要傳入 --payment-password 或設定 WINCHARGE_PAYMENT_PASSWORD 環境變數", file=sys.stderr
        )
        sys.exit(1)

    if not args.json:
        print("⚡ [1/6] 驗證帳號資訊...")
    account = client.get_account_info()
    phone = account.get("contact")
    user_name = account.get("name") or "使用者"
    if not args.json:
        print(f"   └─ 帳號驗證完成 (姓名: {user_name}, Phone: {phone})")

    if args.card_id:
        card_id = args.card_id
        if not args.json:
            print(f"💳 [2/6] 使用指定卡片 ID: {card_id}")
    else:
        if not args.json:
            print("💳 [2/6] 查詢綁定卡片...")
        card_id = client.get_primary_card_id(charger_id)
        if not args.json:
            print(f"   └─ 取得預設卡片 ID: {card_id}")

    if not args.json:
        print("📄 [3/6] 查詢發票設定...")
    invoice = client.get_invoice_setting()
    if not args.json:
        print(f"   └─ 發票載具: {invoice.get('carrierPhone') or '無 (電子郵件發票)'}")

    if not args.json:
        print(f"ℹ️  [4/6] 檢查充電樁即時狀態 (樁號: {charger_id})...")
    charger_info = client.get_charger_info(charger_id, connector=connector)
    site_info = charger_info.get("site_info", {})
    available = charger_info.get("available", True)
    if not args.json:
        print(f"   ├─ 站點名稱: {site_info.get('name', '未知')}")
        print(f"   ├─ 當前費率: NT$ {site_info.get('rate')}/{site_info.get('unit')}")
        print(f"   └─ 可用狀態: {'✅ 可用 (Available)' if available else '⚠️ 告警/充電中 (Unavailable)'}")

    if not available and not args.force:
        msg = f"充電樁 [{charger_id}] 當前顯示不可用 (告警或使用中)。"
        if args.json:
            print(json.dumps({"error": msg, "status": -1, "available": False}, ensure_ascii=False))
        else:
            print(
                f"\n⛔ 啟動預檢攔截：{msg}\n"
                "   為了避免直接建立訂單失敗 (ERROR_CHARGER_IN_USER)，已自動攔截中斷。\n"
                "   💡 如確定槍已插妥並仍要強制建立訂單，請加上 --force 參數再試一次。",
                file=sys.stderr,
            )
        sys.exit(1)

    if not args.json:
        print("🔌 [5/6] 建立充電訂單...")
    order_res = client.create_transaction_order(
        charger_id=charger_id,
        card_id=card_id,
        payment_password=payment_password,
        connector=connector,
    )
    order_id = order_res.get("order_id")
    if order_id:
        save_last_order(order_id)
    if not args.json:
        print(f"   └─ 訂單建立成功！(Order ID: {order_id})")

    if not args.json:
        print(f"🚀 [6/6] 發送啟動充電指令 (Order ID: {order_id})...")
    start_res = client.start_transaction(
        order_id=order_id,
        phone=phone,
        invoice_data=invoice,
    )

    if args.json:
        output = {**start_res, "order_id": order_id}
        print(json.dumps(output, ensure_ascii=False))
    else:
        print("\n🎉 充電樁啟動成功！")
        print(f"   ├─ 訂單編號 (Order ID)       : {start_res.get('order_id', order_id)}")
        print(f"   ├─ 交易編號 (Transaction ID) : {start_res.get('transaction_id')}")
        print(f"   ├─ 槍號 (Connector ID)       : {start_res.get('connector_id')}")
        print(
            f"   ├─ 充電狀態 (Order State)    : {start_res.get('order_state_msg')} (Code: {start_res.get('order_state')})"
        )
        print(f"   ├─ 起始電表度數 (Meter Start): {start_res.get('meter_start')}")
        print(f"   └─ 回應訊息                  : {start_res.get('msg')}")


def handle_status(client: WinChargeClient, args: argparse.Namespace):
    """處理查詢充電狀態 (自動優先線上尋找活躍訂單)"""
    order_id = args.order_id or get_active_order_id(client)
    if not order_id:
        msg = "未指定 order_id，且線上/本機快取均找不到活躍的充電訂單"
        if args.json:
            print(json.dumps({"error": msg, "status": -1}, ensure_ascii=False))
            sys.exit(1)
        else:
            print(f"❌ 錯誤: {msg}", file=sys.stderr)
            sys.exit(1)

    if not args.json:
        print(f"🔍 查詢訂單 [{order_id}] 充電狀態...")

    res = client.get_transaction_status(order_id)

    state_map = {
        1: "準備中",
        2: "充電中",
        3: "已結束",
        4: "已完成 (已結算)",
        5: "異常中斷",
        18: "槍已拔除",
    }
    state_code = res.get("state")
    state_desc = state_map.get(state_code, f"未知狀態 ({state_code})")

    raw_energy = float(res.get("energy", 0.0))
    energy_kwh = round(raw_energy / 1000.0, 3)

    tou_list = parse_tou_map(res.get("tou_price_power_map"))

    if args.json:
        output = {
            **res,
            "order_id": order_id,
            "state_desc": state_desc,
            "energy_kwh": energy_kwh,
            "tou_breakdown": tou_list,
        }
        print(json.dumps(output, ensure_ascii=False))
    else:
        print("\n📊 充電狀態回報：")
        print(f"   ├─ 訂單編號 (Order ID)   : {order_id}")
        print(f"   ├─ 充電樁號 (Charger)   : {res.get('charger')}")
        print(f"   ├─ 槍號 (Connector)     : {res.get('connector')}")
        print(f"   ├─ 狀態 (State)         : {state_desc}")
        print(f"   ├─ 已充電時間 (Duration): {res.get('duration')} 秒")
        print(f"   ├─ 已充電度數 (Energy)  : {energy_kwh} kWh ({raw_energy} Wh)")
        print(f"   ├─ 當前費用 (Fee)       : NT$ {res.get('fee')}")
        print(f"   ├─ 費率說明             : NT$ {res.get('fee_of_unit')} ({res.get('fee_description')})")
        if tou_list:
            print("   └─ 時間電價分時明細 (TOU Breakdown):")
            for t in tou_list:
                print(f"      ├─ 費率 NT$ {t['rate_ntd']}/度: {t['kwh']} kWh (費用: NT$ {t['fee_ntd']})")


def handle_stop(client: WinChargeClient, args: argparse.Namespace):
    """處理停止充電 (自動優先線上尋找活躍訂單)"""
    order_id = args.order_id or get_active_order_id(client)
    if not order_id:
        msg = "未指定 order_id，且線上/本機快取均找不到活躍的充電訂單"
        if args.json:
            print(json.dumps({"error": msg, "status": -1}, ensure_ascii=False))
            sys.exit(1)
        else:
            print(f"❌ 錯誤: {msg}", file=sys.stderr)
            sys.exit(1)

    if not args.json:
        print(f"🛑 發送停止充電指令 (Order ID: {order_id})...")

    res = client.stop_transaction(order_id)
    data = res.get("data", {})

    raw_energy = float(data.get("energy", 0.0))
    energy_kwh = round(raw_energy / 1000.0, 3)
    tou_list = parse_tou_map(data.get("tou_price_power_map"))

    if args.json:
        output = {**res, "order_id": order_id, "energy_kwh": energy_kwh, "tou_breakdown": tou_list}
        print(json.dumps(output, ensure_ascii=False))
    else:
        print("\n✅ 充電已成功停止！")
        print(f"   ├─ 訂單編號 (Order ID)       : {data.get('order_id', order_id)}")
        print(f"   ├─ 狀態 (State)             : State {data.get('state')}")
        print(f"   ├─ 充電時間 (Start ~ End)   : {data.get('start_time')} ~ {data.get('end_time')}")
        print(f"   ├─ 總充電時間 (Duration)    : {data.get('duration')} 秒")
        print(f"   ├─ 總充電度數 (Energy)      : {energy_kwh} kWh ({raw_energy} Wh)")
        print(f"   ├─ 預估費用 (Charge Fee)     : NT$ {data.get('charge_fee')}")
        if tou_list:
            print("   └─ 時間電價分時明細 (TOU Breakdown):")
            for t in tou_list:
                print(f"      ├─ 費率 NT$ {t['rate_ntd']}/度: {t['kwh']} kWh (費用: NT$ {t['fee_ntd']})")


def handle_history(client: WinChargeClient, args: argparse.Namespace):
    """處理查詢歷史充電交易紀錄"""
    page = args.page or 1
    count = args.count or 10

    if not args.json:
        print(f"📜 查詢帳號歷史充電紀錄 (頁碼: {page}, 每頁: {count} 筆)...")

    res = client.get_transaction_history(page=page, page_count=count)

    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        records = res.get("data", res.get("transactions", res.get("list", [])))
        if not isinstance(records, list):
            records = [records]

        state_map = {
            1: "準備中",
            2: "充電中",
            3: "已結束",
            4: "已完成",
            5: "異常中斷",
            18: "槍已拔除",
        }

        print(f"\n📋 歷史充電紀錄清單 (共 {len(records)} 筆)：")
        for item in records:
            order_id = item.get("order_id") or item.get("id") or "未知"
            raw_energy = float(item.get("energy", 0.0))
            kwh = round(raw_energy / 1000.0, 3)
            fee = item.get("fee") or item.get("total_fee") or 0.0

            started_ts = item.get("started")
            if started_ts:
                created_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_ts))
            else:
                created_at = item.get("created_at") or item.get("start_time") or "未知時間"

            state_code = item.get("state")
            state_desc = state_map.get(state_code, f"Code {state_code}")
            charger = item.get("charger") or item.get("charger_id") or ""
            tou_list = parse_tou_map(item.get("tou_price_power_map"))

            print(f"   ├─ [{created_at}] 訂單: {order_id} ({state_desc})")
            print(f"   │  充電樁: {charger} | 度數: {kwh} kWh | 費用: NT$ {fee}")
            if tou_list:
                for t in tou_list:
                    print(f"   │  └─ [分時電價] 費率 NT$ {t['rate_ntd']}/度: {t['kwh']} kWh (NT$ {t['fee_ntd']})")
        print("   └─ 查詢完成。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WinCharge 充電樁 CLI 控制工具 (PEP 723)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--member-id",
        default=os.getenv("WINCHARGE_MEMBER_ID"),
        help="帳號/手機號碼 (可透過環境變數 WINCHARGE_MEMBER_ID 設定)",
    )
    parser.add_argument(
        "--password-hash",
        default=os.getenv("WINCHARGE_PASSWORD_HASH") or os.getenv("WINCHARGE_PASSWORD"),
        help="登入密碼 32 位 MD5 雜湊值 (請直接輸入在瀏覽器 F12 DevTools 擷取的 32 位 MD5 雜湊值，也可透過 WINCHARGE_PASSWORD_HASH 設定)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("WINCHARGE_API_KEY", DEFAULT_API_KEY),
        help="API Key (可透過環境變數 WINCHARGE_API_KEY 設定，預設已置入系統預設值)",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("WINCHARGE_API_TOKEN"),
        help="API Token (選填，可透過環境變數 WINCHARGE_API_TOKEN 設定)",
    )
    parser.add_argument(
        "--api-uid",
        default=os.getenv("WINCHARGE_API_UID"),
        help="API UID (選填，可透過環境變數 WINCHARGE_API_UID 設定)",
    )
    parser.add_argument(
        "--refresh-hours",
        type=int,
        default=int(os.getenv("WINCHARGE_REFRESH_HOURS", "24")),
        help="自訂快取續約週期小時數 (預設: 24 小時，可透過環境變數 WINCHARGE_REFRESH_HOURS 設定)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="開啟 Debug 模式，印出完整的 Raw HTTP Request 與 Response 資訊",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="輸出乾淨的 JSON 格式 (適合 Home Assistant / 自動化腳本解析)",
    )

    subparsers = parser.add_subparsers(dest="command", help="可用的子指令", required=True)

    parser_start = subparsers.add_parser("start", help="開啟充電作業")
    parser_start.add_argument(
        "--charger-id",
        default=os.getenv("WINCHARGE_CHARGER_ID", "wincharge_ocppv16_SAMPLE123"),
        help="充電樁編號 (預設: wincharge_ocppv16_SAMPLE123)",
    )
    parser_start.add_argument(
        "--connector",
        default="",
        help="槍號 (預設留空)",
    )
    parser_start.add_argument(
        "--payment-password",
        default=os.getenv("WINCHARGE_PAYMENT_PASSWORD"),
        help="交易密碼 (可透過環境變數 WINCHARGE_PAYMENT_PASSWORD 設定)",
    )
    parser_start.add_argument(
        "--card-id",
        default=None,
        help="自訂卡片 ID (未指定則自動選取預設卡片)",
    )
    parser_start.add_argument(
        "--force",
        action="store_true",
        help="當充電樁狀態顯示為不可用時，仍強制嘗試建立充電訂單",
    )

    parser_status = subparsers.add_parser("status", help="查詢充電狀態")
    parser_status.add_argument(
        "order_id",
        nargs="?",
        default=None,
        help="訂單編號 (選填，未傳入時自動從線上 API 抓取活躍訂單，或讀取 ~/.wincharge_last_order)",
    )

    parser_stop = subparsers.add_parser("stop", help="停止充電作業")
    parser_stop.add_argument(
        "order_id",
        nargs="?",
        default=None,
        help="訂單編號 (選填，未傳入時自動從線上 API 抓取活躍訂單，或讀取 ~/.wincharge_last_order)",
    )

    parser_history = subparsers.add_parser("history", help="查詢歷史充電紀錄")
    parser_history.add_argument(
        "--page",
        type=int,
        default=1,
        help="頁碼 (預設: 1)",
    )
    parser_history.add_argument(
        "--count",
        type=int,
        default=10,
        help="每頁顯示筆數 (預設: 10)",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    has_credentials = bool(args.member_id and args.password_hash)
    has_tokens = bool(args.api_token and args.api_uid)

    if not has_credentials and not has_tokens:
        parser.error(
            "請提供認證資訊！可以選擇下列兩種方式之一：\n"
            "  1. 提供帳號與密碼雜湊: --member-id / WINCHARGE_MEMBER_ID 與 --password-hash / WINCHARGE_PASSWORD_HASH\n"
            "  2. 提供直接 Token: --api-token / WINCHARGE_API_TOKEN 與 --api-uid / WINCHARGE_API_UID"
        )

    try:
        client = WinChargeClient(
            api_key=args.api_key or DEFAULT_API_KEY,
            api_token=args.api_token,
            api_uid=args.api_uid,
            member_id=args.member_id,
            password=args.password_hash,
            debug=args.debug,
            refresh_hours=args.refresh_hours,
        )
    except Exception as err:
        if getattr(args, "json", False):
            print(json.dumps({"error": f"認證失敗: {err}", "status": -1}, ensure_ascii=False))
        else:
            print(f"❌ 認證失敗: {err}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "start":
            handle_start(client, args)
        elif args.command == "status":
            handle_status(client, args)
        elif args.command == "stop":
            handle_stop(client, args)
        elif args.command == "history":
            handle_history(client, args)
    except Exception as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e), "status": -1}, ensure_ascii=False))
        else:
            print(f"\n❌ 執行失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
