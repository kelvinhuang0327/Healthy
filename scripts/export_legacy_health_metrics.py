from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from healthy.application.legacy_metric_export import (  # noqa: E402
    LegacyExportCompatibilityError,
    LegacyExportError,
    LegacyPersonNotFoundError,
    LegacySchemaIncompatibleError,
    export_legacy_health_metrics_to_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export legacy PersonalHealthOS health metrics to Healthy-compatible CSV."
    )
    parser.add_argument(
        "--legacy-database-url",
        required=True,
        help="Database URL for the legacy PersonalHealthOS database.",
    )
    parser.add_argument(
        "--legacy-person-id",
        required=True,
        help="UUID of the legacy person profile to export.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path where the output CSV will be written.",
    )

    args = parser.parse_args()

    try:
        result = export_legacy_health_metrics_to_file(
            legacy_database_url=args.legacy_database_url,
            legacy_person_id=args.legacy_person_id,
            output_path=args.output,
        )
        output_metadata = {
            "status": "success",
            "total_rows": result.total_rows,
        }
        print(json.dumps(output_metadata))
        return 0
    except LegacyExportCompatibilityError as exc:
        err_metadata = {
            "status": "error",
            "code": exc.code,
            "row": exc.row_number,
            "field": exc.field,
        }
        print(json.dumps(err_metadata), file=sys.stderr)
        return 1
    except LegacyPersonNotFoundError as exc:
        err_metadata = {
            "status": "error",
            "code": exc.code,
            "row": None,
            "field": "legacy_person_id",
        }
        print(json.dumps(err_metadata), file=sys.stderr)
        return 1
    except LegacySchemaIncompatibleError as exc:
        err_metadata = {
            "status": "error",
            "code": exc.code,
            "row": None,
            "field": None,
        }
        print(json.dumps(err_metadata), file=sys.stderr)
        return 1
    except LegacyExportError as exc:
        err_metadata = {
            "status": "error",
            "code": getattr(exc, "code", "LEGACY_EXPORT_ERROR"),
            "row": None,
            "field": None,
        }
        print(json.dumps(err_metadata), file=sys.stderr)
        return 1
    except Exception:
        err_metadata = {
            "status": "error",
            "code": "UNKNOWN_ERROR",
            "row": None,
            "field": None,
        }
        print(json.dumps(err_metadata), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
