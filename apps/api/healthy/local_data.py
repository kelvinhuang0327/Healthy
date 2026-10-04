from __future__ import annotations

import argparse
import fcntl
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from healthy.infrastructure import models as _models  # noqa: F401
from healthy.infrastructure.database import Base

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG = ROOT / "migrations" / "alembic.ini"
BACKUP_DIR_ENV = "HEALTHY_BACKUP_DIR"


class LocalDataError(RuntimeError):
    """A safe, user-facing error for local database operations."""


class RestoreCancelled(LocalDataError):
    """The operator declined a restore before the live database was changed."""


@dataclass(frozen=True, slots=True)
class RestorePlan:
    source: Path
    target: Path
    safety_backup: Path | None


@dataclass(frozen=True, slots=True)
class RestoreResult:
    target: Path
    safety_backup: Path | None


def sqlite_database_path(database_url: str) -> Path:
    try:
        url = make_url(database_url)
    except (ArgumentError, TypeError, ValueError) as error:
        raise LocalDataError(
            "Healthy database URL is invalid; check HEALTHY_DATABASE_URL."
        ) from error

    if (
        url.drivername not in {"sqlite", "sqlite+pysqlite"}
        or not url.database
        or url.database == ":memory:"
        or url.query
    ):
        raise LocalDataError(
            "Local backup and restore require a file-based SQLite database without URL options."
        )

    path = Path(url.database).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _lock_path(database_path: Path) -> Path:
    return database_path.with_name(f"{database_path.name}.healthy.lock")


@contextmanager
def database_operation_lock(database_url: str) -> Iterator[None]:
    database_path = sqlite_database_path(database_url)
    if not database_path.parent.is_dir():
        raise LocalDataError(
            f"Database folder is missing: {database_path.parent}. Run `make local` first."
        )

    lock_path = _lock_path(database_path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise LocalDataError(
            f"Cannot open Healthy's local database lock at {lock_path}: {error.strerror}."
        ) from error

    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise LocalDataError(
                "Healthy is using this database. "
                "Stop the local app, then retry the backup or restore."
            ) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(path.resolve().as_posix(), safe='/')}?mode=ro"


def _script_directory() -> ScriptDirectory:
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(config)


def _validate_database(path: Path, *, require_current_schema: bool) -> None:
    if not path.is_file() or path.is_symlink():
        raise LocalDataError("The selected SQLite file is missing or is not a regular file.")

    try:
        with closing(sqlite3.connect(_sqlite_uri(path), uri=True, timeout=5)) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise LocalDataError("SQLite integrity check failed; the file was not restored.")

            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise LocalDataError(
                    "The SQLite file contains broken references; it was not restored."
                )

            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            minimum_tables = {"accounts", "persons", "sessions", "alembic_version"}
            if not minimum_tables.issubset(tables):
                raise LocalDataError(
                    "This is not a Healthy database backup; required account and migration "
                    "tables are missing."
                )

            revisions = [
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version")
            ]
            if not revisions:
                raise LocalDataError(
                    "The Healthy backup has no migration version and cannot be restored."
                )
            try:
                script_directory = _script_directory()
                revisions_are_known = all(
                    isinstance(revision, str)
                    and script_directory.get_revision(revision) is not None
                    for revision in revisions
                )
            except Exception as error:
                raise LocalDataError(
                    "The backup's migration version is invalid or is not supported by "
                    "this Healthy version."
                ) from error
            if not revisions_are_known:
                raise LocalDataError(
                    "This backup was created by an unknown Healthy version and cannot be "
                    "migrated safely."
                )
            if require_current_schema and not set(Base.metadata.tables).issubset(tables):
                raise LocalDataError(
                    "The local database is incomplete or out of date. "
                    "Run `make local` and try again."
                )
    except sqlite3.Error as error:
        raise LocalDataError(
            "The selected file is not a readable, valid Healthy SQLite database."
        ) from error


def _make_private_directory(directory: Path) -> Path:
    resolved = directory.expanduser().resolve()
    missing: list[Path] = []
    current = resolved
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise LocalDataError(f"Backup location is not a directory: {current}.")
    for path in reversed(missing):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if not path.is_dir():
                raise LocalDataError(f"Backup location is not a directory: {path}.") from None
    if not resolved.is_dir():
        raise LocalDataError(f"Backup location is not a directory: {resolved}.")
    if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
        raise LocalDataError(
            f"Backup folder permissions must be private (0700): {resolved}. "
            "Choose a private folder."
        )
    return resolved


def _new_backup_path(directory: Path, label: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"healthy-{label}-{stamp}-{secrets.token_hex(4)}.sqlite3"


def _copy_database(source: Path, destination: Path) -> None:
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(_sqlite_uri(source), uri=True, timeout=30)
        destination_connection = sqlite3.connect(destination, timeout=30)
        source_connection.backup(destination_connection, pages=256, sleep=0.05)
        destination_connection.commit()
    except sqlite3.Error as error:
        raise LocalDataError(
            "Could not copy the SQLite database; no live database was changed."
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()


def _create_snapshot(
    source: Path,
    backup_directory: Path,
    *,
    label: str,
    destination: Path | None = None,
    require_current_schema: bool,
) -> Path:
    directory = _make_private_directory(backup_directory)
    backup_path = destination or _new_backup_path(directory, label)
    if backup_path.parent.resolve() != directory:
        raise LocalDataError("The backup file must be inside the selected backup folder.")

    descriptor, staged_name = tempfile.mkstemp(
        prefix=".healthy-backup-",
        suffix=".sqlite3",
        dir=directory,
    )
    os.close(descriptor)
    staged = Path(staged_name)
    try:
        _copy_database(source, staged)
        _validate_database(staged, require_current_schema=require_current_schema)
        try:
            os.link(staged, backup_path)
        except FileExistsError as error:
            raise LocalDataError(
                f"Refusing to overwrite the existing backup file: {backup_path}. "
                "Choose another backup folder."
            ) from error
        except OSError as error:
            raise LocalDataError(
                "The backup folder does not support safe, non-overwriting file creation."
            ) from error
        return backup_path
    finally:
        staged.unlink(missing_ok=True)


def create_backup(database_url: str, backup_directory: Path) -> Path:
    database_path = sqlite_database_path(database_url)
    with database_operation_lock(database_url):
        if not database_path.is_file():
            raise LocalDataError(
                f"Healthy database not found at {database_path}. "
                "Run `make local` once before backing up."
            )
        _validate_database(database_path, require_current_schema=True)
        return _create_snapshot(
            database_path,
            backup_directory,
            label="backup",
            require_current_schema=True,
        )


def _restore_source_path(path: Path) -> Path:
    candidate = path.expanduser()
    try:
        file_stat = candidate.lstat()
    except OSError as error:
        raise LocalDataError(f"Backup file cannot be read: {candidate}.") from error
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise LocalDataError("Choose a regular Healthy backup file, not a folder or symbolic link.")
    return candidate.resolve()


def _migration_url(path: Path) -> str:
    return URL.create("sqlite+pysqlite", database=str(path)).render_as_string(hide_password=False)


def _upgrade_staged_database(path: Path) -> None:
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    previous_url = os.environ.get("HEALTHY_DATABASE_URL")
    os.environ["HEALTHY_DATABASE_URL"] = _migration_url(path)
    try:
        command.upgrade(config, "head")
    except Exception as error:
        raise LocalDataError(
            "This Healthy backup could not be upgraded to the installed version; "
            "the current database was left unchanged."
        ) from error
    finally:
        if previous_url is None:
            os.environ.pop("HEALTHY_DATABASE_URL", None)
        else:
            os.environ["HEALTHY_DATABASE_URL"] = previous_url


def _file_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        current = path.stat()
    except FileNotFoundError:
        return None
    return (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)


def _checkpoint_database(database_path: Path) -> None:
    try:
        with closing(sqlite3.connect(database_path, timeout=5)) as connection:
            result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if result is not None and result[0] != 0:
                raise LocalDataError(
                    "The database is still busy. Stop all Healthy processes and retry the restore."
                )
    except sqlite3.Error as error:
        raise LocalDataError(
            "Could not safely close the current SQLite database; it was not replaced."
        ) from error


def restore_database(
    database_url: str,
    backup_file: Path,
    backup_directory: Path,
    *,
    confirm: Callable[[RestorePlan], bool],
) -> RestoreResult:
    database_path = sqlite_database_path(database_url)
    source = _restore_source_path(backup_file)
    if source == database_path:
        raise LocalDataError("The backup file cannot be the live database file.")
    if not database_path.parent.is_dir():
        raise LocalDataError(f"Database folder is missing: {database_path.parent}.")

    with database_operation_lock(database_url):
        current_identity = _file_identity(database_path)
        stage_descriptor, stage_name = tempfile.mkstemp(
            prefix=".healthy-restore-",
            suffix=".sqlite3",
            dir=database_path.parent,
        )
        os.close(stage_descriptor)
        staged = Path(stage_name)
        staged_exists = True
        try:
            _copy_database(source, staged)
            _validate_database(staged, require_current_schema=False)
            _upgrade_staged_database(staged)
            _validate_database(staged, require_current_schema=True)

            safety_backup: Path | None = None
            if current_identity is not None:
                safety_directory = backup_directory.expanduser().resolve()
                safety_backup = _new_backup_path(safety_directory, "before-restore")
            plan = RestorePlan(source=source, target=database_path, safety_backup=safety_backup)
            if not confirm(plan):
                raise RestoreCancelled("Restore cancelled; the current database was not changed.")

            if _file_identity(database_path) != current_identity:
                raise LocalDataError(
                    "The current database changed during confirmation. "
                    "Nothing was restored; review it and retry."
                )
            if current_identity is not None:
                _validate_database(database_path, require_current_schema=False)
                if safety_backup is None:
                    raise LocalDataError("Could not prepare a pre-restore safety copy.")
                _create_snapshot(
                    database_path,
                    backup_directory,
                    label="before-restore",
                    destination=safety_backup,
                    require_current_schema=False,
                )

            if current_identity is not None:
                _checkpoint_database(database_path)
            expected_identity_before_replace = _file_identity(database_path)
            sidecars = [Path(f"{database_path}-wal"), Path(f"{database_path}-shm")]
            if current_identity is None and any(path.exists() for path in sidecars):
                raise LocalDataError(
                    "SQLite sidecar files exist without a database. "
                    "Nothing was restored; inspect the local database first."
                )
            if current_identity is not None:
                for sidecar in sidecars:
                    try:
                        sidecar.unlink(missing_ok=True)
                    except OSError as error:
                        raise LocalDataError(
                            "Could not safely clear SQLite sidecar files; "
                            "the database was not replaced."
                        ) from error

            if _file_identity(database_path) != expected_identity_before_replace:
                raise LocalDataError(
                    "The current database changed before replacement. "
                    "Nothing was restored; review it and retry."
                )
            try:
                os.replace(staged, database_path)
            except OSError as error:
                safety_note = (
                    f" The pre-restore safety copy remains at {safety_backup}."
                    if safety_backup is not None
                    else ""
                )
                raise LocalDataError(
                    "Could not replace the local database atomically; "
                    f"the current database was not changed.{safety_note}"
                ) from error
            staged_exists = False
            return RestoreResult(target=database_path, safety_backup=safety_backup)
        finally:
            if staged_exists:
                staged.unlink(missing_ok=True)


def _backup_directory_from_environment() -> Path:
    configured = os.getenv(BACKUP_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".healthy" / "backups"


def _restore_prompt(plan: RestorePlan) -> bool:
    print("Healthy restore will replace the local database after validating this backup.")
    print(f"Backup file: {plan.source}")
    print(f"Database to replace: {plan.target}")
    if plan.safety_backup is None:
        print("The database does not exist yet; there is no current database to snapshot.")
    else:
        print(f"A private pre-restore safety copy will be saved at: {plan.safety_backup}")
    expected = f"RESTORE {plan.target.name}"
    try:
        response = input(f"Type `{expected}` to continue: ").strip()
    except EOFError:
        response = ""
    if response != expected:
        print("Restore cancelled; the current database was not changed.")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="healthy-local-data",
        description="Create or restore private local Healthy SQLite backups.",
    )
    subcommands = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subcommands.add_parser(
        "prepare",
        help="create or verify the private local Healthy data directory",
    )
    prepare_parser.add_argument(
        "--directory", required=True, type=Path, help="private local Healthy data directory"
    )
    subcommands.add_parser("backup", help="create a new, non-overwriting local backup")
    restore_parser = subcommands.add_parser(
        "restore",
        help="validate and restore a backup after an explicit terminal confirmation",
    )
    restore_parser.add_argument(
        "--file", required=True, type=Path, help="Healthy SQLite backup file"
    )
    args = parser.parse_args(argv)

    try:
        if args.operation == "prepare":
            data_directory = _make_private_directory(args.directory)
            print(f"Healthy local data directory ready: {data_directory}")
            return 0

        database_url = os.getenv("HEALTHY_DATABASE_URL", "")
        backup_directory = _backup_directory_from_environment()
        if args.operation == "backup":
            backup_path = create_backup(database_url, backup_directory)
            print(f"Healthy backup created: {backup_path}")
            print("The SQLite backup is unencrypted; keep it in a private location.")
            return 0

        restore_result = restore_database(
            database_url,
            args.file,
            backup_directory,
            confirm=_restore_prompt,
        )
    except RestoreCancelled:
        return 0
    except LocalDataError as error:
        print(f"Healthy backup/restore error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        reason = error.strerror or "the local path is not accessible"
        print(
            "Healthy backup/restore error: Could not access the local database or backup "
            f"path: {reason}.",
            file=sys.stderr,
        )
        return 2

    print(f"Healthy database restored: {restore_result.target}")
    if restore_result.safety_backup is not None:
        print(f"Pre-restore safety copy: {restore_result.safety_backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
