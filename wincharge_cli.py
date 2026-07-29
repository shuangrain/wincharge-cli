# /// script
# dependencies = [
#   "requests>=2.28.0",
# ]
# ///

"""WinCharge 充電樁 CLI 控制工具 (PEP 723)

單一真相來源 (Single Source of Truth):
核心 API 邏輯與 CLI 介面統一維護於 custom_components/wincharge/wincharge_cli.py
"""

from custom_components.wincharge.wincharge_cli import (
    WinChargeClient,
    build_parser,
    handle_start,
    handle_status,
    handle_stop,
    load_last_order,
    main,
    save_last_order,
    translate_error,
    validate_api_token,
)

__all__ = [
    "WinChargeClient",
    "build_parser",
    "handle_start",
    "handle_status",
    "handle_stop",
    "load_last_order",
    "main",
    "save_last_order",
    "translate_error",
    "validate_api_token",
]

if __name__ == "__main__":
    main()
