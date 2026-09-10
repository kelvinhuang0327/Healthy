from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from healthy.domain import actions as actions_domain
from healthy.domain import external_imports as external_imports_domain
from healthy.domain import metrics as metrics_domain
from healthy.domain import notifications as notifications_domain
from healthy.domain import outcomes as outcomes_domain
from healthy.domain import reminders as reminders_domain
from healthy.domain import symptoms as symptoms_domain
from healthy.infrastructure.database import Base, UTCDateTime


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="status_allowed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
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
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
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
            sqlite_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_account_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(120))
    relationship: Mapped[str] = mapped_column(String(30))
    height_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
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
    report_intakes: Mapped[list[ReportIntakeModel]] = orm_relationship(
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
            " OR steps IS NOT NULL"
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
            "steps IS NULL OR steps BETWEEN"
            f" {metrics_domain.STEPS_MIN} AND {metrics_domain.STEPS_MAX}",
            name="steps_bounds",
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
        CheckConstraint(
            "source_type IN ('manual', 'external_csv')",
            name="source_type_allowed",
        ),
        CheckConstraint(
            "(source_type = 'manual' AND source_record_fingerprint IS NULL)"
            " OR (source_type = 'external_csv'"
            " AND source_record_fingerprint IS NOT NULL"
            " AND length(source_record_fingerprint) = 64)",
            name="source_record_consistent",
        ),
        CheckConstraint(
            "source_record_fingerprint IS NULL OR length(source_record_fingerprint) = 64",
            name="source_record_fingerprint_length",
        ),
        UniqueConstraint(
            "person_id",
            "source_type",
            "source_record_fingerprint",
            name="uq_health_metrics_person_source_record_fingerprint",
        ),
        Index(
            "uq_health_metrics_person_source_fingerprint",
            "person_id",
            "source_type",
            "source_record_fingerprint",
            unique=True,
            postgresql_where=text("source_record_fingerprint IS NOT NULL"),
            sqlite_where=text("source_record_fingerprint IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime())
    systolic_bp_mm_hg: Mapped[int | None] = mapped_column(Integer)
    diastolic_bp_mm_hg: Mapped[int | None] = mapped_column(Integer)
    heart_rate_bpm: Mapped[int | None] = mapped_column(Integer)
    steps: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    blood_glucose_mg_dl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sleep_hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    note: Mapped[str | None] = mapped_column(String(metrics_domain.NOTE_MAX_LENGTH))
    source_type: Mapped[str] = mapped_column(
        String(32),
        default=external_imports_domain.SOURCE_TYPE_MANUAL,
        server_default=text(f"'{external_imports_domain.SOURCE_TYPE_MANUAL}'"),
    )
    source_record_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )


class SymptomLog(Base):
    __tablename__ = "symptom_logs"
    __table_args__ = (
        CheckConstraint(
            f"length(symptom) BETWEEN 1 AND {symptoms_domain.SYMPTOM_MAX_LENGTH}",
            name="symptom_length",
        ),
        CheckConstraint(
            "symptom = trim(symptom)",
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
            "estimated_duration_days IS NULL"
            f" OR estimated_duration_days BETWEEN {symptoms_domain.ESTIMATED_DURATION_DAYS_MIN}"
            f" AND {symptoms_domain.ESTIMATED_DURATION_DAYS_MAX}",
            name="estimated_duration_days_bounds",
        ),
        CheckConstraint(
            f"note IS NULL OR length(note) <= {symptoms_domain.NOTE_MAX_LENGTH}",
            name="note_length",
        ),
        Index(
            "ix_symptom_logs_person_timeline",
            "person_id",
            "occurred_at",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    symptom: Mapped[str] = mapped_column(String(symptoms_domain.SYMPTOM_MAX_LENGTH))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    severity: Mapped[int] = mapped_column(Integer)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    estimated_start_date: Mapped[date | None] = mapped_column(Date)
    estimated_duration_days: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(symptoms_domain.NOTE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    person: Mapped[Person] = orm_relationship(back_populates="symptom_logs")


class HealthAction(Base):
    __tablename__ = "health_actions"
    __table_args__ = (
        CheckConstraint(
            f"length(title) BETWEEN 1 AND {actions_domain.TITLE_MAX_LENGTH}",
            name="title_length",
        ),
        CheckConstraint("title = trim(title)", name="title_trimmed"),
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
            f"description IS NULL OR length(description)"
            f" <= {actions_domain.DESCRIPTION_MAX_LENGTH}",
            name="description_length",
        ),
        CheckConstraint(
            "origin_type IN ('manual', 'action_recommendation')",
            name="origin_type_allowed",
        ),
        CheckConstraint(
            "recommendation_fingerprint IS NULL OR length(recommendation_fingerprint) = 64",
            name="recommendation_fingerprint_length",
        ),
        CheckConstraint(
            "(origin_type = 'manual'"
            " AND recommendation_fingerprint IS NULL"
            " AND recommendation_code IS NULL"
            " AND recommendation_rule_version IS NULL"
            " AND source_rule_code IS NULL"
            " AND source_evidence_kind IS NULL"
            " AND source_evidence_id IS NULL"
            " AND source_observation_id IS NULL"
            " AND source_report_id IS NULL"
            " AND source_evidence_observed_at IS NULL)"
            " OR (origin_type = 'action_recommendation'"
            " AND recommendation_fingerprint IS NOT NULL"
            " AND recommendation_code IS NOT NULL"
            " AND recommendation_rule_version IS NOT NULL"
            " AND source_rule_code IS NOT NULL"
            " AND source_evidence_kind IS NOT NULL"
            " AND source_evidence_id IS NOT NULL"
            " AND source_evidence_observed_at IS NOT NULL)",
            name="recommendation_provenance_consistent",
        ),
        Index(
            "uq_health_actions_person_recommendation_fingerprint",
            "person_id",
            "recommendation_fingerprint",
            unique=True,
        ),
        Index(
            "ix_health_actions_person_timeline",
            "person_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(String(actions_domain.TITLE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(String(actions_domain.DESCRIPTION_MAX_LENGTH))
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    origin_type: Mapped[str] = mapped_column(
        String(32),
        default=actions_domain.HealthActionOriginType.MANUAL,
        server_default=text("'manual'"),
    )
    recommendation_fingerprint: Mapped[str | None] = mapped_column(String(64))
    recommendation_code: Mapped[str | None] = mapped_column(String(128))
    recommendation_rule_version: Mapped[str | None] = mapped_column(String(128))
    source_rule_code: Mapped[str | None] = mapped_column(String(128))
    source_evidence_kind: Mapped[str | None] = mapped_column(String(32))
    source_evidence_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_observation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_report_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_evidence_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(
        String(20),
        default=actions_domain.HealthActionStatus.TODO,
        server_default=text("'todo'"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    person: Mapped[Person] = orm_relationship(back_populates="health_actions")
    outcomes: Mapped[list[HealthActionOutcome]] = orm_relationship(
        back_populates="action",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    reminder: Mapped[HealthActionReminder | None] = orm_relationship(
        back_populates="action",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class HealthActionReminder(Base):
    __tablename__ = "health_action_reminders"
    __table_args__ = (
        UniqueConstraint(
            "action_id",
            name="uq_health_action_reminders_action_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_actions.id", ondelete="CASCADE"),
        nullable=False,
    )
    timezone_name: Mapped[str] = mapped_column(
        String(reminders_domain.MAX_TIMEZONE_NAME_LENGTH),
        nullable=False,
    )
    local_time: Mapped[time] = mapped_column(Time, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_acknowledged_local_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    action: Mapped[HealthAction] = orm_relationship(back_populates="reminder")
    notification_deliveries: Mapped[list[NotificationDelivery]] = orm_relationship(
        back_populates="reminder",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('email')",
            name="channel_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'cancelled', 'failed', 'unknown')",
            name="status_allowed",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL",
            name="sent_requires_sent_at",
        ),
        CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name="failed_requires_failed_at",
        ),
        CheckConstraint(
            "status <> 'sending' OR claimed_at IS NOT NULL",
            name="sending_requires_claimed_at",
        ),
        UniqueConstraint(
            "reminder_id",
            "channel",
            "reminder_local_date",
            name="uq_notification_deliveries_reminder_channel_local_date",
        ),
        Index(
            "ix_notification_deliveries_status_claimed_at",
            "status",
            "claimed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    reminder_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_action_reminders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=notifications_domain.NotificationChannel.EMAIL,
        server_default=text("'email'"),
    )
    reminder_local_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=notifications_domain.NotificationDeliveryStatus.PENDING,
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    reminder: Mapped[HealthActionReminder] = orm_relationship(
        back_populates="notification_deliveries",
    )


class HealthActionOutcome(Base):
    __tablename__ = "health_action_outcomes"
    __table_args__ = (
        CheckConstraint(
            f"length(note) BETWEEN 1 AND {outcomes_domain.NOTE_MAX_LENGTH}",
            name="note_length",
        ),
        CheckConstraint("note = trim(note)", name="note_trimmed"),
        Index(
            "ix_health_action_outcomes_action_timeline",
            "action_id",
            "observed_at",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    action_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_actions.id", ondelete="CASCADE"),
        index=True,
    )
    note: Mapped[str] = mapped_column(String(outcomes_domain.NOTE_MAX_LENGTH))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
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
        UniqueConstraint(
            "person_id",
            "canonical_sha256",
            name="uq_health_reports_person_sha256",
        ),
        Index(
            "ix_health_reports_person_timeline",
            "person_id",
            "reported_at",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(128))
    reported_at: Mapped[datetime] = mapped_column(UTCDateTime())
    canonical_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

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
            "observed_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_reports.id", ondelete="CASCADE"),
        index=True,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    code: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    reference_range: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )

    report: Mapped[HealthReportModel] = orm_relationship(back_populates="observations")


class ReportIntakeModel(Base):
    __tablename__ = "report_intakes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'confirmed', 'failed')",
            name="ck_report_intakes_status",
        ),
        CheckConstraint(
            "pending_review = (status = 'pending_review')",
            name="ck_report_intakes_pending_review_consistent",
        ),
        CheckConstraint(
            "status <> 'confirmed' OR report_id IS NOT NULL",
            name="ck_report_intakes_confirmed_requires_report",
        ),
        UniqueConstraint(
            "person_id",
            "file_sha256",
            name="uq_report_intakes_person_file_sha256",
        ),
        Index(
            "ix_report_intakes_person_timeline",
            "person_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        index=True,
    )
    source_filename: Mapped[str] = mapped_column(String(255))
    source_name: Mapped[str] = mapped_column(String(128))
    file_sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(64))
    extraction_method: Mapped[str] = mapped_column(String(32))
    parser_version: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_character_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(24),
        default="pending_review",
        server_default=text("'pending_review'"),
    )
    pending_review: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("1"),
    )
    reported_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_message: Mapped[str | None] = mapped_column(String(256))
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("health_reports.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    person: Mapped[Person] = orm_relationship(back_populates="report_intakes")
    report: Mapped[HealthReportModel | None] = orm_relationship()
    observations: Mapped[list[ReportIntakeObservationModel]] = orm_relationship(
        back_populates="intake",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReportIntakeObservationModel.ordinal",
    )

    @property
    def parser_metadata(self) -> dict[str, int | str | None]:
        return {
            "parser_version": self.parser_version,
            "page_count": self.page_count,
            "extracted_character_count": self.extracted_character_count,
        }


class ReportIntakeObservationModel(Base):
    __tablename__ = "report_intake_observations"
    __table_args__ = (
        CheckConstraint(
            "value_numeric IS NOT NULL OR "
            "(value_text IS NOT NULL AND length(trim(value_text)) > 0)",
            name="ck_report_intake_observations_at_least_one_value",
        ),
        CheckConstraint(
            "parser_confidence IS NULL OR parser_confidence BETWEEN 0 AND 1",
            name="ck_report_intake_observations_confidence_bounds",
        ),
        UniqueConstraint(
            "intake_id",
            "ordinal",
            name="uq_report_intake_observations_intake_ordinal",
        ),
        Index(
            "ix_report_intake_observations_intake_order",
            "intake_id",
            "ordinal",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    intake_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("report_intakes.id", ondelete="CASCADE"),
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    value_numeric: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    value_text: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    reference_range: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    parser_provenance: Mapped[str] = mapped_column(String(128))
    parser_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    intake: Mapped[ReportIntakeModel] = orm_relationship(back_populates="observations")
