from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from healthy.domain.identity import PersonRelationship


class AccountCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class SessionCreate(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class PersonCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    relationship: PersonRelationship = PersonRelationship.FAMILY


class AccountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    normalized_email: EmailStr
    status: str
    created_at: datetime


class PersonSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_account_id: uuid.UUID
    display_name: str
    relationship: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class SessionSummary(BaseModel):
    id: uuid.UUID
    account: AccountSummary
    expires_at: datetime


class RegistrationResponse(BaseModel):
    account: AccountSummary
    default_person: PersonSummary
    session: SessionSummary
