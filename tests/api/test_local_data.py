from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from healthy.infrastructure import models as _models  # noqa: F401
from healthy.infrastructure.database import Base
from healthy.local_data import (
    LocalDataError,
    RestoreCancelled,
    RestorePlan,
    _restore_prompt,
    create_backup,
    database_operation_lock,
    restore_database,
    sqlite_database_path,
)
from healthy.local_data import (
    main as local_data_main,
)
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[2]


def _database_url(path: Path) -> str:
    return URL.create("sqlite+pysqlite", database=str(path)).render_as_string(hide_password=False)


def _create_healthy_database(path: Path, marker: str) -> None:
    engine = create_engine(_database_url(path))
    try:
        Base.metadata.create_all(engine)
        config = Config(str(ROOT / "migrations" / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        head = ScriptDirectory.from_config(config).get_current_head()
        assert head is not None
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:version)"),
                {"version": head},
            )
            connection.execute(text("CREATE TABLE synthetic_probe (value TEXT NOT NULL)"))
            connection.execute(
                text("INSERT INTO synthetic_probe(value) VALUES (:value)"),
                {"value": marker},
            )
    finally:
        engine.dispose()


def _marker(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        result = connection.execute("SELECT value FROM synthetic_probe").fetchone()
    assert result is not None
    return str(result[0])


def test_prepare_command_creates_private_local_data_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_directory = tmp_path / ".healthy"

    assert local_data_main(["prepare", "--directory", str(data_directory)]) == 0

    assert data_directory.is_dir()
    assert stat.S_IMODE(data_directory.stat().st_mode) == 0o700
    assert "Healthy local data directory ready" in capsys.readouterr().out


def test_backup_creates_private_non_overwriting_sqlite_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "healthy.db"
    backup_directory = tmp_path / "private-backups"
    _create_healthy_database(database, "synthetic source")

    first = create_backup(_database_url(database), backup_directory)
    second = create_backup(_database_url(database), backup_directory)

    assert first != second
    assert first.is_file() and second.is_file()
    assert stat.S_IMODE(first.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup_directory.stat().st_mode) == 0o700
    assert _marker(first) == "synthetic source"
    with sqlite3.connect(first) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_backup_refuses_a_backup_folder_open_to_other_users(tmp_path: Path) -> None:
    database = tmp_path / "healthy.db"
    backup_directory = tmp_path / "shared-backups"
    _create_healthy_database(database, "synthetic source")
    backup_directory.mkdir(mode=0o700)
    backup_directory.chmod(0o755)

    with pytest.raises(LocalDataError, match="permissions must be private"):
        create_backup(_database_url(database), backup_directory)

    assert list(backup_directory.iterdir()) == []


def test_backup_and_restore_require_the_app_database_lock(tmp_path: Path) -> None:
    database = tmp_path / "healthy.db"
    source = tmp_path / "selected.sqlite3"
    _create_healthy_database(database, "synthetic source")
    _create_healthy_database(source, "synthetic backup")
    database_url = _database_url(database)

    with database_operation_lock(database_url):
        with pytest.raises(LocalDataError, match="Stop the local app"):
            create_backup(database_url, tmp_path / "backups")
        with pytest.raises(LocalDataError, match="Stop the local app"):
            restore_database(
                database_url,
                source,
                tmp_path / "backups",
                confirm=lambda _plan: pytest.fail("restore prompted while the app lock was held"),
            )


def test_backup_collision_does_not_overwrite_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "healthy.db"
    backup_directory = tmp_path / "private-backups"
    backup_directory.mkdir(mode=0o700)
    occupied = backup_directory / "reserved.sqlite3"
    occupied.write_bytes(b"keep existing synthetic file")
    _create_healthy_database(database, "synthetic source")
    monkeypatch.setattr(
        "healthy.local_data._new_backup_path",
        lambda _directory, _label: occupied,
    )

    with pytest.raises(LocalDataError, match="Refusing to overwrite"):
        create_backup(_database_url(database), backup_directory)

    assert occupied.read_bytes() == b"keep existing synthetic file"


def test_restore_prompt_requires_exact_database_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = RestorePlan(
        source=tmp_path / "backup.sqlite3",
        target=tmp_path / "healthy.db",
        safety_backup=tmp_path / "healthy-before-restore.sqlite3",
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "RESTORE healthy.db")
    assert _restore_prompt(plan)
    monkeypatch.setattr("builtins.input", lambda _prompt: "RESTORE")
    assert not _restore_prompt(plan)


def test_cancelled_restore_does_not_change_database_or_create_safety_copy(
    tmp_path: Path,
) -> None:
    database = tmp_path / "healthy.db"
    source = tmp_path / "selected.sqlite3"
    backup_directory = tmp_path / "private-backups"
    _create_healthy_database(database, "current synthetic data")
    _create_healthy_database(source, "backup synthetic data")

    seen_plans = []
    with pytest.raises(RestoreCancelled):
        restore_database(
            _database_url(database),
            source,
            backup_directory,
            confirm=lambda plan: seen_plans.append(plan) or False,
        )

    assert len(seen_plans) == 1
    assert seen_plans[0].target == database.resolve()
    assert seen_plans[0].safety_backup is not None
    assert _marker(database) == "current synthetic data"
    assert not backup_directory.exists()
    assert not list(tmp_path.glob(".healthy-restore-*.sqlite3"))


def test_restore_keeps_pre_restore_copy_and_uses_validated_backup(tmp_path: Path) -> None:
    database = tmp_path / "healthy.db"
    source = tmp_path / "selected.sqlite3"
    backup_directory = tmp_path / "private-backups"
    _create_healthy_database(database, "current synthetic data")
    _create_healthy_database(source, "backup synthetic data")
    plans = []

    result = restore_database(
        _database_url(database),
        source,
        backup_directory,
        confirm=lambda plan: plans.append(plan) or True,
    )

    assert len(plans) == 1
    assert result.target == database.resolve()
    assert result.safety_backup == plans[0].safety_backup
    assert result.safety_backup is not None and result.safety_backup.is_file()
    assert stat.S_IMODE(result.safety_backup.stat().st_mode) == 0o600
    assert _marker(database) == "backup synthetic data"
    assert _marker(result.safety_backup) == "current synthetic data"


def test_invalid_restore_file_leaves_database_untouched(tmp_path: Path) -> None:
    database = tmp_path / "healthy.db"
    invalid_backup = tmp_path / "not-a-database.sqlite3"
    invalid_backup.write_text("synthetic invalid input", encoding="utf-8")
    _create_healthy_database(database, "current synthetic data")
    confirmation_called = False

    def confirm(_plan) -> bool:
        nonlocal confirmation_called
        confirmation_called = True
        return True

    with pytest.raises(LocalDataError):
        restore_database(
            _database_url(database),
            invalid_backup,
            tmp_path / "private-backups",
            confirm=confirm,
        )

    assert not confirmation_called
    assert _marker(database) == "current synthetic data"
    assert not (tmp_path / "private-backups").exists()


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+pysqlite:///:memory:",
        "postgresql+psycopg://example.invalid/healthy",
        "not a database URL",
    ],
)
def test_local_backup_rejects_non_file_or_non_sqlite_configuration(database_url: str) -> None:
    with pytest.raises(LocalDataError):
        sqlite_database_path(database_url)
