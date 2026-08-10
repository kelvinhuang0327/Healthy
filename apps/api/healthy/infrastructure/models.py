from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from healthy.domain import actions as actions_domain
from healthy.domain import metrics as metrics_domain
from healthy.domain import outcomes as outcomes_domain
from healthy.domain import symptoms as symptoms_domain
from healthy.infrastructure.database import Base


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="status_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    sessions: Mapped[list[SessionRecord]] = orm_relationship(
        back_populates="account",
        cascade="all, delete-orphan",
    )
    persons: Mapped[list[Person]] = orm_relationship(
        back_populates="owner",
        cascade="all, delete-orphan",
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    account: Mapped[Account] = orm_relationship(back_populates="sessions")


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = (
        CheckConstraint(
            "NOT is_default OR relationship = 'self'",
            name="default_relationship_self",
        ),
        Index(
            "uq_persons_one_default_per_account",
            "owner_account_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(120))
    relationship: Mapped[str] = mapped_column(String(30))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    owner: Mapped[Account] = orm_relationship(back_populates="persons")
    symptom_logs: Mapped[list[SymptomLog]] = orm_relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    health_actions: Mapped[list[HealthAction]] = orm_relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    health_reports: Mapped[list[HealthReportModel]] = orm_relationship(
        back_populates="person",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HealthMetric(Base):
    __tablename__ = "health_metrics"
    __table_args__ = (
        CheckConstraint(
            "(systolic_bp_mm_hg IS NULL) = (diastolic_bp_mm_hg IS NULL)",
            name="bp_pairing",
        ),
        CheckConstraint(
            "systolic_bp_mm_hg IS NOT NULL"
            " OR diastolic_bp_mm_hg IS NOT NULL"
            " OR heart_rate_bpm IS NOT NULL"
            " OR weight_kg IS NOT NULL"
            " OR blood_glucose_mg_dl IS NOT NULL"
            " OR sleep_hours IS NOT NULL",
            name="at_least_one_value",
        ),
        CheckConstraint(
            "systolic_bp_mm_hg IS NULL OR systolic_bp_mm_hg BETWEEN"
            f" {metrics_domain.SYSTOLIC_BP_MM_HG_MIN} AND {metrics_domain.SYSTOLIC_BP_MM_HG_MAX}",
            name="systolic_bp_mm_hg_bounds",
        ),
        CheckConstraint(
            "diastolic_bp_mm_hg IS NULL OR diastolic_bp_mm_hg BETWEEN"
            f" {metrics_domain.DIASTOLIC_BP_MM_HG_MIN} AND {metrics_domain.DIASTOLIC_BP_MM_HG_MAX}",
            name="diastolic_bp_mm_hg_bounds",
        ),
        CheckConstraint(
            "heart_rate_bpm IS NULL OR heart_rate_bpm BETWEEN"
            f" {metrics_domain.HEART_RATE_BPM_MIN} AND {metrics_domain.HEART_RATE_BPM_MAX}",
            name="heart_rate_bpm_bounds",
        ),
        CheckConstraint(
            "weight_kg IS NULL OR weight_kg BETWEEN"
            f" {metrics_domain.WEIGHT_KG_MIN} AND {metrics_domain.WEIGHT_KG_MAX}",
            name="weight_kg_bounds",
        ),
        CheckConstraint(
            "blood_glucose_mg_dl IS NULL OR blood_glucose_mg_dl BETWEEN"
            f" {metrics_domain.BLOOD_GLUCOSE_MG_DL_MIN}"
            f" AND {metrics_domain.BLOOD_GLUCOSE_MG_DL_MAX}",
            name="blood_glucose_mg_dl_bounds",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    systolic_bp_mm_hg: Mapped[int | None] = mapped_column(Integer)
    diastolic_bp_mm_hg: Mapped[int | None] = mapped_column(Integer)
    heart_rate_bpm: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    blood_glucose_mg_dl: Mapped[Decimal | None] = mapped_column(Numeric(5, 1))
    sleep_hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    note: Mapped[str | None] = mapped_column(String(metrics_domain.NOTE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class SymptomLog(Base):
    __tablename__ = "symptom_logs"
    __table_args__ = (
        CheckConstraint(
            f"char_length(symptom) BETWEEN 1 AND {symptoms_domain.SYMPTOM_MAX_LENGTH}",
            name="symptom_length",
        ),
        CheckConstraint(
            "symptom = btrim(symptom)",
            name="symptom_trimmed",
        ),
        CheckConstraint(
            f"severity BETWEEN {symptoms_domain.SEVERITY_MIN} AND {symptoms_domain.SEVERITY_MAX}",
            name="severity_bounds",
        ),
        CheckConstraint(
            "duration_minutes IS NULL"
            f" OR duration_minutes >= {symptoms_domain.DURATION_MINUTES_MIN}",
            name="duration_minutes_minimum",
        ),
        CheckConstraint(
            f"note IS NULL OR char_length(note) <= {symptoms_domain.NOTE_MAX_LENGTH}",
            name="note_length",
        ),
        Index(
            "ix_symptom_logs_person_timeline",
            "person_id",
            text("occurred_at DESC"),
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    symptom: Mapped[str] = mapped_column(String(symptoms_domain.SYMPTOM_MAX_LENGTH))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    severity: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(symptoms_domain.NOTE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    person: Mapped[Person] = orm_relationship(back_populates="symptom_logs")


class HealthAction(Base):
    __tablename__ = "health_actions"
    __table_args__ = (
        CheckConstraint(
            f"char_length(title) BETWEEN 1 AND {actions_domain.TITLE_MAX_LENGTH}",
            name="title_length",
        ),
        CheckConstraint("title = btrim(title)", name="title_trimmed"),
        CheckConstraint(
            "status IN ('todo', 'done')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'todo' AND completed_at IS NULL)"
            " OR (status = 'done' AND completed_at IS NOT NULL)",
            name="status_completion_consistent",
        ),
        CheckConstraint(
            f"description IS NULL OR char_length(description)"
            f" <= {actions_domain.DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
        Index(
            "ix_health_actions_person_timeline",
            "person_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(actions_domain.TITLE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(String(actions_domain.DESCRIPTION_MAX_LENGTH))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(20),
        default=actions_domain.HealthActionStatus.TODO,
        server_default=text("'todo'"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    person: Mapped[Person] = orm_relationship(back_populates="health_actions")
    outcomes: Mapped[list[HealthActionOutcome]] = orm_relationship(
        back_populates="action",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HealthActionOutcome(Base):
    __tablename__ = "health_action_outcomes"
    __table_args__ = (
        CheckConstraint(
            f"char_length(note) BETWEEN 1 AND {outcomes_domain.NOTE_MAX_LENGTH}",
            name="note_length",
        ),
        CheckConstraint("note = btrim(note)", name="note_trimmed"),
        Index(
            "ix_health_action_outcomes_action_timeline",
            "action_id",
            text("observed_at DESC"),
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("health_actions.id", ondelete="CASCADE"),
        index=True,
    )
    note: Mapped[str] = mapped_column(String(outcomes_domain.NOTE_MAX_LENGTH))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    action: Mapped[HealthAction] = orm_relationship(back_populates="outcomes")


class HealthReportModel(Base):
    __tablename__ = "health_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed')",
            name="ck_health_reports_status",
        ),
        Index(
            "uq_health_reports_person_sha256",
            "person_id",
            "canonical_sha256",
            unique=True,
        ),
        Index(
            "ix_health_reports_person_timeline",
            "person_id",
            text("reported_at DESC"),
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(128))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    canonical_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person: Mapped[Person] = orm_relationship(back_populates="health_reports")
    observations: Mapped[list[HealthReportObservationModel]] = orm_relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HealthReportObservationModel(Base):
    __tablename__ = "health_report_observations"
    __table_args__ = (
        Index(
            "ix_health_report_observations_person_code",
            "person_id",
            "code",
            text("observed_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("health_reports.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    code: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    reference_range: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    report: Mapped[HealthReportModel] = orm_relationship(back_populates="observations")
