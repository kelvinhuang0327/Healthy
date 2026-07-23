from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from export_openapi import CONTRACT_PATH, rendered_contract  # noqa: E402


def main() -> int:
    if not CONTRACT_PATH.exists():
        print(f"Missing checked-in contract: {CONTRACT_PATH}")
        return 1
    checked_in = CONTRACT_PATH.read_text(encoding="utf-8")
    rendered = rendered_contract()
    if checked_in != rendered:
        print("Checked-in OpenAPI contract differs from runtime OpenAPI")
        return 1
    print("OpenAPI contract matches runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
