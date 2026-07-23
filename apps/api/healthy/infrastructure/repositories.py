from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthy.infrastructure.models import Person


class PersonRepository:
    @staticmethod
    def list_for_owner(database_session: Session, owner_account_id: uuid.UUID) -> list[Person]:
        statement = (
            select(Person)
            .where(Person.owner_account_id == owner_account_id)
            .order_by(Person.is_default.desc(), Person.created_at.asc())
        )
        return list(database_session.scalars(statement))

    @staticmethod
    def get_for_owner(
        database_session: Session,
        owner_account_id: uuid.UUID,
        person_id: uuid.UUID,
    ) -> Person | None:
        statement = select(Person).where(
            Person.id == person_id,
            Person.owner_account_id == owner_account_id,
        )
        return database_session.scalar(statement)

    @staticmethod
    def create_non_default(
        database_session: Session,
        owner_account_id: uuid.UUID,
        display_name: str,
        relationship: str,
    ) -> Person:
        person = Person(
            owner_account_id=owner_account_id,
            display_name=display_name,
            relationship=relationship,
            is_default=False,
        )
        database_session.add(person)
        return person
