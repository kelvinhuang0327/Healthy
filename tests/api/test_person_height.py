from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import DATABASE_URL, ORIGIN, csrf_headers, register
from fastapi.testclient import TestClient
from healthy.infrastructure.database import Database
from healthy.infrastructure.models import Person
from sqlalchemy import select


def test_height_is_nullable_persisted_and_survives_a_new_session(
    client: TestClient,
) -> None:
    assert register(client, email="height-owner@example.com").status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]

    empty = client.get(f"/v1/persons/{person_id}")
    assert empty.status_code == 200
    assert empty.json()["height_cm"] is None

    updated = client.patch(
        f"/v1/persons/{person_id}/profile",
        headers=csrf_headers(client),
        json={"height_cm": 173.25},
    )
    assert updated.status_code == 200
    assert updated.json()["height_cm"] == 173.25

    database = Database(DATABASE_URL)
    with next(database.sessions()) as database_session:
        person = database_session.scalar(select(Person).where(Person.id == person_id))
        assert person is not None
        assert person.height_cm == Decimal("173.25")

    client.cookies.clear()
    logged_in = client.post(
        "/v1/sessions",
        headers={"Origin": ORIGIN},
        json={
            "email": "height-owner@example.com",
            "password": "Synthetic-Password-42",
        },
    )
    assert logged_in.status_code == 200
    persisted = client.get(f"/v1/persons/{person_id}")
    assert persisted.status_code == 200
    assert persisted.json()["height_cm"] == 173.25

    cleared = client.patch(
        f"/v1/persons/{person_id}/profile",
        headers=csrf_headers(client),
        json={"height_cm": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["height_cm"] is None


@pytest.mark.parametrize("height_cm", ["not-a-number", "NaN", "Infinity", 0, -1])
def test_height_rejects_non_numeric_non_finite_and_non_positive_values(
    client: TestClient,
    height_cm: object,
) -> None:
    assert register(client, email="height-validation@example.com").status_code == 201
    person_id = client.get("/v1/persons").json()[0]["id"]

    response = client.patch(
        f"/v1/persons/{person_id}/profile",
        headers=csrf_headers(client),
        json={"height_cm": height_cm},
    )
    assert response.status_code == 422
    assert client.get(f"/v1/persons/{person_id}").json()["height_cm"] is None


def test_height_update_is_person_isolated(client: TestClient) -> None:
    assert register(client, email="height-owner-a@example.com").status_code == 201
    person_a_id = client.get("/v1/persons").json()[0]["id"]

    other = TestClient(client.app, base_url=ORIGIN)
    assert register(other, email="height-owner-b@example.com").status_code == 201
    person_b_id = other.get("/v1/persons").json()[0]["id"]

    foreign_read = client.get(f"/v1/persons/{person_b_id}")
    foreign_update = client.patch(
        f"/v1/persons/{person_b_id}/profile",
        headers=csrf_headers(client),
        json={"height_cm": 180},
    )
    assert foreign_read.status_code == foreign_update.status_code == 404
    assert other.get(f"/v1/persons/{person_b_id}").json()["height_cm"] is None
    assert client.get(f"/v1/persons/{person_a_id}").json()["height_cm"] is None
