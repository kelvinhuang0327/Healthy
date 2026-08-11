from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.application import services
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Account, Person, SessionRecord, SymptomLog
from sqlalchemy import func, select


def _person_id(client: TestClient) -> str:
    return client.get("/v1/persons").json()[0]["id"]


def _create_symptom(client: TestClient, person_id: str, **overrides: object):
    payload = {
        "symptom": "Headache",
        "occurred_at": datetime.now(UTC).isoformat(),
        "severity": 3,
        "duration_minutes": None,
        "estimated_start_date": None,
        "estimated_duration_days": None,
        "note": None,
    }
    payload.update(overrides)
    return client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client),
        json=payload,
    )


def test_create_historical_list_and_single_fetch_newest_first(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    older = _create_symptom(
        client,
        person_id,
        symptom="  Headache  ",
        occurred_at="2026-01-01T08:00:00+08:00",
        severity=1,
        estimated_start_date="2025-07-01",
        estimated_duration_days=180,
        note="Historical entry",
    )
    newer = _create_symptom(
        client,
        person_id,
        symptom="Nausea",
        occurred_at="2026-06-01T00:00:00Z",
        severity=5,
        duration_minutes=30,
    )
    assert older.status_code == newer.status_code == 201
    assert older.json()["symptom"] == "Headache"
    assert older.json()["occurred_at"] == "2026-01-01T00:00:00Z"
    assert older.json()["duration_minutes"] is None
    assert older.json()["estimated_start_date"] == "2025-07-01"
    assert older.json()["estimated_duration_days"] == 180
    assert newer.json()["duration_minutes"] == 30
    assert newer.json()["estimated_start_date"] is None
    assert newer.json()["estimated_duration_days"] is None

    listing = client.get(f"/v1/persons/{person_id}/symptoms")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()] == [
        newer.json()["id"],
        older.json()["id"],
    ]

    single = client.get(f"/v1/persons/{person_id}/symptoms/{newer.json()['id']}")
    assert single.status_code == 200
    assert single.json() == newer.json()


def test_create_rejects_future_and_timezone_naive_occurred_at(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()

    assert _create_symptom(client, person_id, occurred_at=future).status_code == 422
    assert _create_symptom(client, person_id, occurred_at="2026-01-01T00:00:00").status_code == 422


def test_severity_accepts_bounds_and_rejects_values_outside_scale(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    assert _create_symptom(client, person_id, severity=1).status_code == 201
    assert _create_symptom(client, person_id, severity=5).status_code == 201
    assert _create_symptom(client, person_id, severity=0).status_code == 422
    assert _create_symptom(client, person_id, severity=6).status_code == 422


def test_duration_is_optional_but_must_be_at_least_one_when_present(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)

    assert _create_symptom(client, person_id).status_code == 201
    assert _create_symptom(client, person_id, duration_minutes=1).status_code == 201
    assert _create_symptom(client, person_id, duration_minutes=0).status_code == 422
    assert _create_symptom(client, person_id, estimated_duration_days=1).status_code == 201
    assert _create_symptom(client, person_id, estimated_duration_days=36500).status_code == 201
    assert _create_symptom(client, person_id, estimated_duration_days=0).status_code == 422
    assert _create_symptom(client, person_id, estimated_duration_days=36501).status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symptom", "   "),
        ("symptom", "x" * 121),
        ("note", "x" * 2001),
    ],
)
def test_text_validation(client: TestClient, field: str, value: str) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    assert _create_symptom(client, person_id, **{field: value}).status_code == 422


def test_endpoints_require_authentication(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    client.cookies.clear()

    assert (
        client.post(
            f"/v1/persons/{person_id}/symptoms",
            headers={"Origin": ORIGIN},
            json={
                "symptom": "Cough",
                "occurred_at": datetime.now(UTC).isoformat(),
                "severity": 2,
            },
        ).status_code
        == 401
    )
    assert client.get(f"/v1/persons/{person_id}/symptoms").status_code == 401
    assert client.get(f"/v1/persons/{person_id}/symptoms/{uuid.uuid4()}").status_code == 401


def test_create_requires_valid_origin_and_csrf(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    payload = {
        "symptom": "Cough",
        "occurred_at": datetime.now(UTC).isoformat(),
        "severity": 2,
    }

    missing_csrf = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers={"Origin": ORIGIN},
        json=payload,
    )
    invalid_csrf = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "invalid"},
        json=payload,
    )
    invalid_origin = client.post(
        f"/v1/persons/{person_id}/symptoms",
        headers=csrf_headers(client, origin="https://attacker.example"),
        json=payload,
    )
    assert missing_csrf.status_code == invalid_csrf.status_code == 403
    assert invalid_origin.status_code == 403


def test_symptoms_are_owner_scoped_with_foreign_resource_404s(client: TestClient) -> None:
    assert register(client, email="owner-a@example.com").status_code == 201
    person_a = _person_id(client)
    symptom_a = _create_symptom(client, person_a)
    assert symptom_a.status_code == 201

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="owner-b@example.com").status_code == 201
    person_b = _person_id(other)
    symptom_b = _create_symptom(other, person_b, symptom="Fatigue")
    assert symptom_b.status_code == 201

    assert _create_symptom(other, person_a).status_code == 404
    assert other.get(f"/v1/persons/{person_a}/symptoms").status_code == 404
    foreign_person_get = other.get(f"/v1/persons/{person_a}/symptoms/{symptom_a.json()['id']}")
    foreign_record_get = other.get(f"/v1/persons/{person_b}/symptoms/{symptom_a.json()['id']}")
    assert foreign_person_get.status_code == foreign_record_get.status_code == 404
    assert symptom_a.json()["id"] not in foreign_person_get.text
    assert symptom_a.json()["id"] not in foreign_record_get.text


def test_missing_person_and_symptom_record_return_404(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    missing_person = str(uuid.uuid4())
    missing_symptom = uuid.uuid4()

    assert _create_symptom(client, missing_person).status_code == 404
    assert client.get(f"/v1/persons/{missing_person}/symptoms").status_code == 404
    assert client.get(f"/v1/persons/{missing_person}/symptoms/{missing_symptom}").status_code == 404
    assert client.get(f"/v1/persons/{person_id}/symptoms/{missing_symptom}").status_code == 404


def test_repeated_gets_produce_zero_database_writes(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = _person_id(client)
    created = _create_symptom(client, person_id)
    assert created.status_code == 201
    symptom_id = created.json()["id"]
    database = Database(DATABASE_URL)

    def snapshot() -> tuple[list[tuple[object, ...]], ...]:
        with next(database.sessions()) as database_session:
            return (
                list(
                    database_session.execute(
                        select(Account.id, Account.updated_at).order_by(Account.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(Person.id, Person.updated_at).order_by(Person.id)
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(SessionRecord.id, SessionRecord.expires_at).order_by(
                            SessionRecord.id
                        )
                    ).tuples()
                ),
                list(
                    database_session.execute(
                        select(
                            SymptomLog.id,
                            SymptomLog.occurred_at,
                            SymptomLog.created_at,
                        ).order_by(SymptomLog.id)
                    ).tuples()
                ),
            )

    before = snapshot()
    for _ in range(3):
        assert client.get(f"/v1/persons/{person_id}/symptoms").status_code == 200
        assert client.get(f"/v1/persons/{person_id}/symptoms/{symptom_id}").status_code == 200
    assert snapshot() == before


def test_deleting_person_cascades_symptom_logs(client: TestClient) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    assert _create_symptom(client, str(person_id)).status_code == 201
    database = Database(DATABASE_URL)

    with next(database.sessions()) as database_session:
        person = database_session.get(Person, person_id)
        assert person is not None
        database_session.delete(person)
        database_session.commit()
        assert database_session.scalar(select(func.count()).select_from(SymptomLog)) == 0


def test_database_constraints_are_mapped_to_generic_integrity_error(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    person_id = uuid.UUID(_person_id(client))
    database = Database(DATABASE_URL)

    with next(database.sessions()) as database_session:
        with pytest.raises(services.SymptomLogIntegrityError):
            services.create_symptom_log(
                database_session,
                owner_account_id=database_session.scalar(select(Account.id)),
                person_id=person_id,
                symptom="Cough",
                occurred_at=datetime.now(UTC),
                severity=6,
                duration_minutes=None,
                note=None,
            )
        assert database_session.scalar(select(func.count()).select_from(SymptomLog)) == 0
