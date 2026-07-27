from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from healthy.main import app  # noqa: E402

CONTRACT_PATH = ROOT / "contracts" / "openapi" / "healthy-v1.yaml"


def rendered_contract() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT_PATH.write_text(rendered_contract(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
