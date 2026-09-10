from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from healthy.application.notification_delivery import (  # noqa: E402
    process_notification_delivery_tick,
)
from healthy.infrastructure.config import Settings  # noqa: E402
from healthy.infrastructure.database import Database  # noqa: E402
from healthy.infrastructure.email import SMTPEmailTransport  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process one bounded email delivery tick")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Permit the configured SMTP transport to be invoked",
    )
    parser.add_argument(
        "--max-deliveries",
        type=int,
        default=100,
        help="Maximum pending rows to claim in this tick",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.max_deliveries < 1:
        print("status=configuration_error")
        return 2

    try:
        settings = Settings.from_env()
    except RuntimeError:
        print("status=configuration_error")
        return 2

    database = Database(settings.database_url)
    session_iterator = database.sessions()
    database_session = next(session_iterator)
    try:
        result = process_notification_delivery_tick(
            database_session,
            settings=settings,
            send=args.send,
            transport=SMTPEmailTransport(settings) if args.send else None,
            max_deliveries=args.max_deliveries,
        )
    except ValueError:
        print("status=configuration_error")
        return 2
    finally:
        database_session.close()
        database.engine.dispose()

    print(
        " ".join(
            [
                "status=ok" if result.capability_available else "status=capability_unavailable",
                f"enqueued={result.enqueued}",
                f"stale_reconciled={result.stale_reconciled}",
                f"claimed={result.claimed}",
                f"sent={result.sent}",
                f"cancelled={result.cancelled}",
                f"failed={result.failed}",
                f"skipped_send={result.skipped_send}",
            ]
        )
    )
    return 0 if result.capability_available else 2


if __name__ == "__main__":
    raise SystemExit(main())
