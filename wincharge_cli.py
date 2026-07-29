# /// script
# dependencies = [
#   "requests>=2.28.0",
# ]
# ///

"""WinCharge 充電樁 CLI 控制工具 (PEP 723)

⚠️ 免責聲明 (Disclaimer):
    本工具僅供個人技術測試、研究與學習使用。使用本工具進行任何 API 呼叫、充電作業衍生之費用、
    設備損害或法律責任，開發者不負任何形式之責任。請確保在合法與授權環境下使用。

支援子指令:
    start   : 執行完整預檢與啟動流程以開始充電
    status  : 查詢指定訂單的充電狀態
    stop    : 停止指定訂單的充電

使用範例:
    # 1. 開啟充電
    uv run wincharge_cli.py start --payment-password "123456" --charger-id "wincharge_ocppv16_SAMPLE123"

    # 2. 查詢狀態
    uv run wincharge_cli.py status 2400000000SAMPLE123

    # 3. 停止充電
    uv run wincharge_cli.py stop 2400000000SAMPLE123

支援環境變數預設值:
    WINCHARGE_API_KEY
    WINCHARGE_API_TOKEN
    WINCHARGE_API_UID
    WINCHARGE_PAYMENT_PASSWORD
    WINCHARGE_CHARGER_ID
"""

import argparse
import base64
import json
import os
import sys
import time
from typing import Any, Dict, Optional
import requests

BASE_URL = "https://new-home.wincharge.net"


def validate_api_token(token: str) -> Dict[str, Any]:
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
    except Exception as e:
        raise ValueError(f"API Token 解碼失敗: {e}")

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
    def __init__(self, api_key: str, api_token: str, api_uid: str):
        # 驗證 JWT Token
        self.jwt_payload = validate_api_token(api_token)

        self.session = requests.Session()
        self.session.headers.update({
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
        })
        self.session.cookies.set("i18n_redirected", "zh-TW")

    # -------------------------------------------------------------------------
    # 帳號與預檢 API
    # -------------------------------------------------------------------------

    def get_account_info(self) -> Dict[str, Any]:
        """Step 1: 取得帳號資訊並驗證 contact 欄位"""
        url = f"{BASE_URL}/api/account"
        headers = {"referer": f"{BASE_URL}/user/account/settings"}

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"帳號資訊取得失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

        contact = data.get("contact", "")
        if not contact or not str(contact).strip():
            raise ValueError("帳號驗證失敗：聯絡電話/資訊 (contact) 為空")

        return data

    def get_primary_card_id(self, charger_id: str) -> str:
        """Step 2: 取得卡片清單，確認有卡片並回傳預設卡片 ID"""
        url = f"{BASE_URL}/api/account/cards"
        headers = {"referer": f"{BASE_URL}/charger/{charger_id}"}

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"卡片列表取得失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

        cards = data.get("cards", [])
        if not cards or len(cards) == 0:
            raise ValueError("卡片驗證失敗：帳號內沒有已綁定的支付卡片 (cards 長度為 0)")

        # 優先尋找 primary == True 的卡片，若無則取第一張
        primary_card = next((c for c in cards if c.get("primary") is True), cards[0])
        card_id = primary_card.get("id")
        if not card_id:
            raise ValueError("無效的卡片資料：找不到卡片 ID")

        return card_id

    def get_invoice_setting(self) -> Dict[str, Any]:
        """Step 3: 取得發票設定資訊"""
        url = f"{BASE_URL}/api/account/invoice"
        headers = {"referer": f"{BASE_URL}/user/account/settings"}

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"發票設定取得失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

        invoice = data.get("invoice")
        if not invoice or not isinstance(invoice, dict):
            raise ValueError("發票設定驗證失敗：未取得有效的 invoice 物件")

        return invoice

    def get_charger_info(self, charger_id: str, connector: str = "") -> Dict[str, Any]:
        """Step 4: 查詢充電樁即時狀態與站點資訊"""
        url = f"{BASE_URL}/api/chargers/{charger_id}?connector={connector}"
        headers = {"referer": f"{BASE_URL}/charger/{charger_id}"}

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("code") != 0 and data.get("status") not in (0, None):
            raise RuntimeError(f"充電樁資訊查詢失敗: code={data.get('code')}")

        return data

    # -------------------------------------------------------------------------
    # 充電交易 API
    # -------------------------------------------------------------------------

    def create_transaction_order(
        self, charger_id: str, card_id: str, payment_password: str, connector: str = ""
    ) -> Dict[str, Any]:
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

        response = self.session.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"建立充電訂單失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

        return data

    def start_transaction(
        self, order_id: str, phone: str, invoice_data: Dict[str, Any]
    ) -> Dict[str, Any]:
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

        response = self.session.put(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"發送啟動充電指令失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

        return data

    def get_transaction_status(self, order_id: str) -> Dict[str, Any]:
        """查詢交易/充電狀態"""
        url = f"{BASE_URL}/api/transactions/{order_id}"
        headers = {"referer": f"{BASE_URL}/transaction/{order_id}"}

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    def stop_transaction(self, order_id: str) -> Dict[str, Any]:
        """停止充電交易"""
        url = f"{BASE_URL}/api/transactions/{order_id}/stop"
        headers = {
            "origin": BASE_URL,
            "referer": f"{BASE_URL}/transaction/{order_id}",
            "content-length": "0",
        }

        response = self.session.put(url, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != 0:
            raise RuntimeError(f"停止充電失敗: {data.get('error_msg', '未知錯誤')} (status: {data.get('status')})")

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
        print("❌ 錯誤: 開始充電需要傳入 --payment-password 或設定 WINCHARGE_PAYMENT_PASSWORD 環境變數", file=sys.stderr)
        sys.exit(1)

    print("⚡ [1/6] 驗證帳號資訊...")
    account = client.get_account_info()
    phone = account.get("contact")
    user_name = account.get("name") or "使用者"
    print(f"   └─ 帳號驗證完成 (姓名: {user_name}, Phone: {phone})")

    if args.card_id:
        card_id = args.card_id
        print(f"💳 [2/6] 使用指定卡片 ID: {card_id}")
    else:
        print("💳 [2/6] 查詢綁定卡片...")
        card_id = client.get_primary_card_id(charger_id)
        print(f"   └─ 取得預設卡片 ID: {card_id}")

    print("📄 [3/6] 查詢發票設定...")
    invoice = client.get_invoice_setting()
    print(f"   └─ 發票載具: {invoice.get('carrierPhone') or '無 (電子郵件發票)'}")

    print(f"ℹ️  [4/6] 檢查充電樁即時狀態 (樁號: {charger_id})...")
    charger_info = client.get_charger_info(charger_id, connector=connector)
    site_info = charger_info.get("site_info", {})
    available = charger_info.get("available", True)
    print(f"   ├─ 站點名稱: {site_info.get('name', '未知')}")
    print(f"   ├─ 當前費率: NT$ {site_info.get('rate')}/{site_info.get('unit')}")
    print(f"   └─ 可用狀態: {'✅ 可用 (Available)' if available else '⚠️ 告警/充電中 (Unavailable)'}")

    print(f"🔌 [5/6] 建立充電訂單...")
    order_res = client.create_transaction_order(
        charger_id=charger_id,
        card_id=card_id,
        payment_password=payment_password,
        connector=connector,
    )
    order_id = order_res.get("order_id")
    print(f"   └─ 訂單建立成功！(Order ID: {order_id})")

    print(f"🚀 [6/6] 發送啟動充電指令 (Order ID: {order_id})...")
    start_res = client.start_transaction(
        order_id=order_id,
        phone=phone,
        invoice_data=invoice,
    )

    print("\n🎉 充電樁啟動成功！")
    print(f"   ├─ 訂單編號 (Order ID)       : {start_res.get('order_id', order_id)}")
    print(f"   ├─ 交易編號 (Transaction ID) : {start_res.get('transaction_id')}")
    print(f"   ├─ 槍號 (Connector ID)       : {start_res.get('connector_id')}")
    print(f"   ├─ 充電狀態 (Order State)    : {start_res.get('order_state_msg')} (Code: {start_res.get('order_state')})")
    print(f"   ├─ 起始電表度數 (Meter Start): {start_res.get('meter_start')}")
    print(f"   └─ 回應訊息                  : {start_res.get('msg')}")


def handle_status(client: WinChargeClient, args: argparse.Namespace):
    """處理查詢充電狀態"""
    order_id = args.order_id
    print(f"🔍 查詢訂單 [{order_id}] 充電狀態...")

    res = client.get_transaction_status(order_id)

    state_map = {1: "準備中", 2: "充電中", 3: "已結束"}
    state_code = res.get("state")
    state_desc = state_map.get(state_code, f"未知狀態 ({state_code})")

    print("\n📊 充電狀態回報：")
    print(f"   ├─ 充電樁號 (Charger)   : {res.get('charger')}")
    print(f"   ├─ 槍號 (Connector)     : {res.get('connector')}")
    print(f"   ├─ 狀態 (State)         : {state_desc}")
    print(f"   ├─ 已充電時間 (Duration): {res.get('duration')} 秒")
    print(f"   ├─ 已充電度數 (Energy)  : {res.get('energy')} kWh")
    print(f"   ├─ 當前費用 (Fee)       : NT$ {res.get('fee')}")
    print(f"   └─ 費率說明             : NT$ {res.get('fee_of_unit')} ({res.get('fee_description')})")


def handle_stop(client: WinChargeClient, args: argparse.Namespace):
    """處理停止充電"""
    order_id = args.order_id
    print(f"🛑 發送停止充電指令 (Order ID: {order_id})...")

    res = client.stop_transaction(order_id)
    data = res.get("data", {})

    print("\n✅ 充電已成功停止！")
    print(f"   ├─ 訂單編號 (Order ID)       : {data.get('order_id', order_id)}")
    print(f"   ├─ 狀態 (State)             : State {data.get('state')}")
    print(f"   ├─ 充電時間 (Start ~ End)   : {data.get('start_time')} ~ {data.get('end_time')}")
    print(f"   ├─ 總充電時間 (Duration)    : {data.get('duration')} 秒")
    print(f"   ├─ 總充電度數 (Energy)      : {data.get('energy')} kWh")
    print(f"   └─ 預估費用 (Charge Fee)     : NT$ {data.get('charge_fee')}")


# -------------------------------------------------------------------------
# CLI 參數解析與主進入點
# -------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WinCharge 充電樁 CLI 控制工具 (PEP 723)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # 全域/認證參數
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

    subparsers = parser.add_subparsers(dest="command", help="可用的子指令", required=True)

    # 子指令 1: start
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

    # 子指令 2: status
    parser_status = subparsers.add_parser("status", help="查詢充電狀態")
    parser_status.add_argument(
        "order_id",
        help="訂單編號 (Order ID，例如: 2400000000SAMPLE123)",
    )

    # 子指令 3: stop
    parser_stop = subparsers.add_parser("stop", help="停止充電作業")
    parser_stop.add_argument(
        "order_id",
        help="訂單編號 (Order ID，例如: 2400000000SAMPLE123)",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # 驗證全域認證頭
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
        )
    except ValueError as val_err:
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
        print(f"\n❌ 執行失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
