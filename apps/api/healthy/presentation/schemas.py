from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from healthy.domain import metrics as metrics_domain
from healthy.domain import symptoms as symptoms_domain
from healthy.domain.identity import PersonRelationship

JsonDecimal = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]


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


class HealthMetricCreate(BaseModel):
    recorded_at: datetime
    systolic_bp_mm_hg: int | None = Field(
        default=None,
        ge=metrics_domain.SYSTOLIC_BP_MM_HG_MIN,
        le=metrics_domain.SYSTOLIC_BP_MM_HG_MAX,
    )
    diastolic_bp_mm_hg: int | None = Field(
        default=None,
        ge=metrics_domain.DIASTOLIC_BP_MM_HG_MIN,
        le=metrics_domain.DIASTOLIC_BP_MM_HG_MAX,
    )
    heart_rate_bpm: int | None = Field(
        default=None,
        ge=metrics_domain.HEART_RATE_BPM_MIN,
        le=metrics_domain.HEART_RATE_BPM_MAX,
    )
    weight_kg: (
        Annotated[
            JsonDecimal,
            Field(
                ge=metrics_domain.WEIGHT_KG_MIN,
                le=metrics_domain.WEIGHT_KG_MAX,
                decimal_places=metrics_domain.WEIGHT_KG_DECIMAL_PLACES,
            ),
        ]
        | None
    ) = None
    blood_glucose_mg_dl: (
        Annotated[
            JsonDecimal,
            Field(
                ge=metrics_domain.BLOOD_GLUCOSE_MG_DL_MIN,
                le=metrics_domain.BLOOD_GLUCOSE_MG_DL_MAX,
                decimal_places=metrics_domain.BLOOD_GLUCOSE_MG_DL_DECIMAL_PLACES,
            ),
        ]
        | None
    ) = None
    note: str | None = Field(default=None, max_length=metrics_domain.NOTE_MAX_LENGTH)

    @field_validator("recorded_at")
    @classmethod
    def _normalize_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("recorded_at must include timezone information")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _validate_invariants(self) -> HealthMetricCreate:
        if not metrics_domain.has_at_least_one_metric_value(
            systolic_bp_mm_hg=self.systolic_bp_mm_hg,
            diastolic_bp_mm_hg=self.diastolic_bp_mm_hg,
            heart_rate_bpm=self.heart_rate_bpm,
            weight_kg=self.weight_kg,
            blood_glucose_mg_dl=self.blood_glucose_mg_dl,
        ):
            raise ValueError("At least one metric value is required")
        if not metrics_domain.blood_pressure_is_paired(
            self.systolic_bp_mm_hg,
            self.diastolic_bp_mm_hg,
        ):
            raise ValueError(
                "systolic_bp_mm_hg and diastolic_bp_mm_hg must both be present or both absent"
            )
        if self.recorded_at > datetime.now(UTC) + metrics_domain.RECORDED_AT_MAX_FUTURE_SKEW:
            raise ValueError("recorded_at cannot be more than five minutes in the future")
        return self


class HealthMetricSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    recorded_at: datetime
    systolic_bp_mm_hg: int | None
    diastolic_bp_mm_hg: int | None
    heart_rate_bpm: int | None
    weight_kg: JsonDecimal | None
    blood_glucose_mg_dl: JsonDecimal | None
    note: str | None
    created_at: datetime


class SymptomLogCreate(BaseModel):
    symptom: str = Field(min_length=1, max_length=symptoms_domain.SYMPTOM_MAX_LENGTH)
    occurred_at: datetime
    severity: int = Field(
        ge=symptoms_domain.SEVERITY_MIN,
        le=symptoms_domain.SEVERITY_MAX,
    )
    duration_minutes: int | None = Field(
        default=None,
        ge=symptoms_domain.DURATION_MINUTES_MIN,
    )
    note: str | None = Field(default=None, max_length=symptoms_domain.NOTE_MAX_LENGTH)

    @field_validator("symptom")
    @classmethod
    def _normalize_symptom(cls, value: str) -> str:
        return symptoms_domain.normalize_symptom(value)

    @field_validator("occurred_at")
    @classmethod
    def _normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include timezone information")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC) + symptoms_domain.OCCURRED_AT_MAX_FUTURE_SKEW:
            raise ValueError("occurred_at cannot be more than five minutes in the future")
        return normalized


class SymptomLogSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    symptom: str
    occurred_at: datetime
    severity: int
    duration_minutes: int | None
    note: str | None
    created_at: datetime
