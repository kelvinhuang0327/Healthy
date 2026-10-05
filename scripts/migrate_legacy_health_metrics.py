from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from healthy.application.legacy_metric_migration import (  # noqa: E402
    LegacyMetricMigrationError,
    run_legacy_metric_migration,
    serialize_migration_error,
    serialize_migration_summary,
)


class _MigrationArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise _InvalidCliArguments


class _InvalidCliArguments(Exception):
    pass


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = _MigrationArgumentParser(
        description="Run the manifest-driven Healthy legacy HealthMetric migration."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--validate-only",
        dest="mode",
        action="store_const",
        const="validate_only",
    )
    mode_group.add_argument(
        "--execute",
        dest="mode",
        action="store_const",
        const="execute",
    )
    parser.set_defaults(mode="validate_only")
    parser.add_argument("--confirm-write")
    parser.add_argument("--expected-mapping-count", type=_non_negative_integer)
    parser.add_argument("--expected-total-rows", type=_non_negative_integer)
    parser.add_argument("--expected-target-revision")
    return parser


def _read_manifest_from_stdin() -> bytes | str:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    payload = stream.read()
    if isinstance(payload, str):
        return payload
    return bytes(payload)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except _InvalidCliArguments:
        print(
            '{"status":"error","code":"INVALID_CLI_ARGUMENTS"}',
            file=sys.stderr,
        )
        return 1

    try:
        summary = run_legacy_metric_migration(
            _read_manifest_from_stdin(),
            mode=args.mode,
            confirm_write=args.confirm_write,
            expected_mapping_count=args.expected_mapping_count,
            expected_total_rows=args.expected_total_rows,
            expected_target_revision=args.expected_target_revision,
        )
    except LegacyMetricMigrationError as error:
        print(serialize_migration_error(error), file=sys.stderr)
        return 1
    except Exception:
        print('{"status":"error","code":"MIGRATION_FAILED"}', file=sys.stderr)
        return 1

    print(serialize_migration_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
