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
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://new-home.wincharge.net"
LAST_ORDER_FILE = Path.home() / ".wincharge_last_order"

# 錯誤訊息對照字典
ERROR_TRANSLATION_MAP = {
    "ERROR_CHARGER_IN_USER": "充電樁目前正由其他使用者佔用或正在充電中",
    "ERROR_CHARGER_OFFLINE": "充電樁目前處於離線狀態，無法對外通訊",
    "ERROR_PAYMENT_PASSWORD": "交易密碼驗證失敗，請確認密碼是否正確",
    "ERROR_CARD_INVALID": "指定的支付卡片無效或已被停用",
    "ERROR_NO_CARD": "帳號內未繫結有效的支付卡片",
    "ERROR_UNAUTHORIZED": "認證標頭失效或 Token 已過期",
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
    status_str = f" (status: {status})" if status is not None else ""

    if explanation:
        return f"{explanation} [{msg}]{status_str}"
    return f"{msg}{status_str}"


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


class WinChargeClient:
    def __init__(self, api_key: str, api_token: str, api_uid: str, debug: bool = False):
        # 驗證 JWT Token
        self.jwt_payload = validate_api_token(api_token)
        self.debug = debug

        self.session = requests.Session()
        self.session.headers.update(
            {
                "accept": "application/json, text/plain, */*",
                "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
                "cache-control": "no-cache",
                "pragma": "no-cache",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
                ),
                "x-api-key": api_key,
                "x-api-token": api_token,
                "x-api-uid": api_uid,
            }
        )
        self.session.cookies.set("i18n_redirected", "zh-TW")

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """發送 HTTP 請求並支援 --debug 詳細日誌列印"""
        if self.debug:
            print("\n" + "=" * 60)
            print(f"🐛 [DEBUG REQUEST] HTTP {method.upper()} {url}")
            merged_headers = {**self.session.headers, **kwargs.get("headers", {})}
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
    """處理查詢充電狀態"""
    order_id = args.order_id or load_last_order()
    if not order_id:
        msg = "未指定 order_id，且本機找不到歷史啟動紀錄檔 (~/.wincharge_last_order)"
        if args.json:
            print(json.dumps({"error": msg, "status": -1}, ensure_ascii=False))
            sys.exit(1)
        else:
            print(f"❌ 錯誤: {msg}", file=sys.stderr)
            sys.exit(1)

    if not args.json:
        print(f"🔍 查詢訂單 [{order_id}] 充電狀態...")

    res = client.get_transaction_status(order_id)

    state_map = {1: "準備中", 2: "充電中", 3: "已結束"}
    state_code = res.get("state")
    state_desc = state_map.get(state_code, f"未知狀態 ({state_code})")

    if args.json:
        output = {**res, "order_id": order_id, "state_desc": state_desc}
        print(json.dumps(output, ensure_ascii=False))
    else:
        print("\n📊 充電狀態回報：")
        print(f"   ├─ 訂單編號 (Order ID)   : {order_id}")
        print(f"   ├─ 充電樁號 (Charger)   : {res.get('charger')}")
        print(f"   ├─ 槍號 (Connector)     : {res.get('connector')}")
        print(f"   ├─ 狀態 (State)         : {state_desc}")
        print(f"   ├─ 已充電時間 (Duration): {res.get('duration')} 秒")
        print(f"   ├─ 已充電度數 (Energy)  : {res.get('energy')} kWh")
        print(f"   ├─ 當前費用 (Fee)       : NT$ {res.get('fee')}")
        print(f"   └─ 費率說明             : NT$ {res.get('fee_of_unit')} ({res.get('fee_description')})")


def handle_stop(client: WinChargeClient, args: argparse.Namespace):
    """處理停止充電"""
    order_id = args.order_id or load_last_order()
    if not order_id:
        msg = "未指定 order_id，且本機找不到歷史啟動紀錄檔 (~/.wincharge_last_order)"
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

    if args.json:
        output = {**res, "order_id": order_id}
        print(json.dumps(output, ensure_ascii=False))
    else:
        print("\n✅ 充電已成功停止！")
        print(f"   ├─ 訂單編號 (Order ID)       : {data.get('order_id', order_id)}")
        print(f"   ├─ 狀態 (State)             : State {data.get('state')}")
        print(f"   ├─ 充電時間 (Start ~ End)   : {data.get('start_time')} ~ {data.get('end_time')}")
        print(f"   ├─ 總充電時間 (Duration)    : {data.get('duration')} 秒")
        print(f"   ├─ 總充電度數 (Energy)      : {data.get('energy')} kWh")
        print(f"   └─ 預估費用 (Charge Fee)     : NT$ {data.get('charge_fee')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WinCharge 充電樁 CLI 控制工具 (PEP 723)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--api-key",
        default=os.getenv("WINCHARGE_API_KEY"),
        help="API Key (可透過環境變數 WINCHARGE_API_KEY 設定)",
    )
    parser.add_argument(
        "--api-token",
        default=os.getenv("WINCHARGE_API_TOKEN"),
        help="API Token (可透過環境變數 WINCHARGE_API_TOKEN 設定)",
    )
    parser.add_argument(
        "--api-uid",
        default=os.getenv("WINCHARGE_API_UID"),
        help="API UID (可透過環境變數 WINCHARGE_API_UID 設定)",
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
        help="訂單編號 (選填，未傳入時自動讀取最新紀錄 ~/.wincharge_last_order)",
    )

    parser_stop = subparsers.add_parser("stop", help="停止充電作業")
    parser_stop.add_argument(
        "order_id",
        nargs="?",
        default=None,
        help="訂單編號 (選填，未傳入時自動讀取最新紀錄 ~/.wincharge_last_order)",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    missing_auth = []
    if not args.api_key:
        missing_auth.append("--api-key / WINCHARGE_API_KEY")
    if not args.api_token:
        missing_auth.append("--api-token / WINCHARGE_API_TOKEN")
    if not args.api_uid:
        missing_auth.append("--api-uid / WINCHARGE_API_UID")

    if missing_auth:
        parser.error("缺少認證標頭參數:\n  - " + "\n  - ".join(missing_auth))

    try:
        client = WinChargeClient(
            api_key=args.api_key,
            api_token=args.api_token,
            api_uid=args.api_uid,
            debug=args.debug,
        )
    except ValueError as val_err:
        if getattr(args, "json", False):
            print(json.dumps({"error": f"JWT 認證頭驗證失敗: {val_err}", "status": -1}, ensure_ascii=False))
        else:
            print(f"❌ JWT 認證頭驗證失敗: {val_err}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "start":
            handle_start(client, args)
        elif args.command == "status":
            handle_status(client, args)
        elif args.command == "stop":
            handle_stop(client, args)
    except Exception as e:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(e), "status": -1}, ensure_ascii=False))
        else:
            print(f"\n❌ 執行失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
