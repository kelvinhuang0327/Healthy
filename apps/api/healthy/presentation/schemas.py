from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

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

from healthy.domain import actions as actions_domain
from healthy.domain import metrics as metrics_domain
from healthy.domain import outcomes as outcomes_domain
from healthy.domain import reminders as reminders_domain
from healthy.domain import symptoms as symptoms_domain
from healthy.domain.identity import PersonRelationship

JsonDecimal = Annotated[
    Decimal,
    PlainSerializer(lambda value: float(value), return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]

HeightCm = Annotated[
    JsonDecimal,
    Field(max_digits=5, decimal_places=2),
]

SleepHours = Annotated[
    JsonDecimal,
    Field(
        max_digits=metrics_domain.SLEEP_HOURS_MAX_DIGITS,
        decimal_places=metrics_domain.SLEEP_HOURS_DECIMAL_PLACES,
    ),
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


class PersonHeightUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    height_cm: HeightCm | None

    @field_validator("height_cm")
    @classmethod
    def validate_height_cm(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value <= 0):
            raise ValueError("height_cm must be a finite number greater than zero")
        return value


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
    height_cm: HeightCm | None
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
    steps: int | None = Field(
        default=None,
        ge=metrics_domain.STEPS_MIN,
        le=metrics_domain.STEPS_MAX,
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
    sleep_hours: SleepHours | None = None
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
            steps=self.steps,
            weight_kg=self.weight_kg,
            blood_glucose_mg_dl=self.blood_glucose_mg_dl,
            sleep_hours=self.sleep_hours,
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
    steps: int | None
    weight_kg: JsonDecimal | None
    blood_glucose_mg_dl: JsonDecimal | None
    sleep_hours: SleepHours | None
    note: str | None
    source_type: str = "manual"
    created_at: datetime


class ExternalMetricCsvImportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: Literal["external_csv"] = "external_csv"
    total_rows: int
    imported_count: int
    duplicate_count: int


class HealthAnalyticsMetricSummary(BaseModel):
    metric: str
    label: str
    unit: str
    points: int
    first_value: float | None
    last_value: float | None
    change_percent: float | None
    slope_per_day: float | None
    direction: Literal["up", "down", "stable", "no_data"]


class HealthAnalyticsSummary(BaseModel):
    period_days: int
    summaries: list[HealthAnalyticsMetricSummary]


class HealthScoreComponentSummary(BaseModel):
    kind: Literal["cardiovascular", "metabolic", "activity", "weight", "overall"]
    label: str
    points: int
    penalty: int
    evidence_ids: list[uuid.UUID]
    rationale: str


class HealthScoreCoverageSummary(BaseModel):
    evaluated_inputs: list[str]
    missing_inputs: list[str]
    unsupported_sources: list[str]


class HealthScoreSummary(BaseModel):
    score: int
    status: Literal["stable", "monitor", "attention", "insufficient_data"]
    rule_version: str
    anchor_at: datetime | None
    data_points: int
    components: list[HealthScoreComponentSummary]
    coverage: HealthScoreCoverageSummary
    limitations: str


class RiskAlertEvidenceSummary(BaseModel):
    source_kind: Literal["health_metric", "lab_report"]
    source_id: uuid.UUID
    person_id: uuid.UUID
    observed_at: datetime
    observation_id: uuid.UUID | None
    report_id: uuid.UUID | None
    report_source_name: str | None


class RiskAlertSummary(BaseModel):
    rule_code: str
    risk_type: str
    severity: Literal["medium", "high"]
    status: Literal["active"]
    evidence: RiskAlertEvidenceSummary


class RiskAlertsSummary(BaseModel):
    active_count: int
    alerts: list[RiskAlertSummary]


class ActionRecommendationSummary(BaseModel):
    recommendation_code: str
    source_rule_code: str
    source_risk_type: str
    source_severity: Literal["medium", "high"]
    title: str
    rationale: str
    suggested_action: str
    matching_alert_count: int
    rule_version: str
    limitations: str
    evidence: RiskAlertEvidenceSummary


class ActionRecommendationsSummary(BaseModel):
    recommendations: list[ActionRecommendationSummary]


class ActionRecommendationAcceptanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_version: str = Field(min_length=1, max_length=128)
    source_kind: Literal["health_metric", "lab_report"]
    source_id: uuid.UUID
    observation_id: uuid.UUID | None = None
    report_id: uuid.UUID | None = None
    observed_at: datetime

    @field_validator("rule_version")
    @classmethod
    def _normalize_rule_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("rule_version must not be blank")
        return normalized

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include timezone information")
        return value.astimezone(UTC)


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
    estimated_start_date: date | None = None
    estimated_duration_days: int | None = Field(
        default=None,
        ge=symptoms_domain.ESTIMATED_DURATION_DAYS_MIN,
        le=symptoms_domain.ESTIMATED_DURATION_DAYS_MAX,
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
    estimated_start_date: date | None
    estimated_duration_days: int | None
    note: str | None
    created_at: datetime


class HealthActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=actions_domain.TITLE_MAX_LENGTH)
    description: str | None = Field(
        default=None,
        max_length=actions_domain.DESCRIPTION_MAX_LENGTH,
    )
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        return actions_domain.normalize_title(value)

    @field_validator("description")
    @classmethod
    def _normalize_description(cls, value: str | None) -> str | None:
        return actions_domain.normalize_description(value)

    @field_validator("due_at")
    @classmethod
    def _normalize_due_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("due_at must include timezone information")
        return value.astimezone(UTC)


class HealthActionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    title: str
    description: str | None
    due_at: datetime | None
    origin_type: actions_domain.HealthActionOriginType
    recommendation_code: str | None
    recommendation_rule_version: str | None
    source_rule_code: str | None
    source_evidence_kind: Literal["health_metric", "lab_report"] | None
    source_evidence_id: uuid.UUID | None
    source_observation_id: uuid.UUID | None
    source_report_id: uuid.UUID | None
    source_evidence_observed_at: datetime | None
    status: actions_domain.HealthActionStatus
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class HealthActionReminderUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone_name: str = Field(
        min_length=1,
        max_length=reminders_domain.MAX_TIMEZONE_NAME_LENGTH,
    )
    local_time: time

    @field_validator("timezone_name")
    @classmethod
    def _validate_timezone_name(cls, value: str) -> str:
        try:
            return reminders_domain.validate_timezone(value)
        except ValueError as error:
            raise ValueError("timezone_name must be a valid IANA timezone") from error

    @field_validator("local_time")
    @classmethod
    def _validate_local_time(cls, value: time) -> time:
        try:
            return reminders_domain.normalize_local_time(value)
        except ValueError as error:
            raise ValueError("local_time must not include a timezone offset") from error


class HealthActionReminderEmailChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class HealthActionReminderSnooze(BaseModel):
    model_config = ConfigDict(extra="forbid")

    until: datetime

    @field_validator("until")
    @classmethod
    def _normalize_until(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("until must include timezone information")
        return value.astimezone(UTC)


class HealthActionReminderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_id: uuid.UUID
    timezone_name: str
    local_time: time
    email_enabled: bool
    snoozed_until: datetime | None
    last_acknowledged_local_date: date | None
    created_at: datetime
    updated_at: datetime


class NotificationCapabilitiesSummary(BaseModel):
    email_available: bool


class DueHealthActionReminderSummary(BaseModel):
    reminder_id: uuid.UUID
    action_id: uuid.UUID
    action_title: str
    action_origin_type: actions_domain.HealthActionOriginType
    timezone_name: str
    local_time: time
    local_date: date
    snoozed_until: datetime | None
    last_acknowledged_local_date: date | None


class ActionRecommendationAcceptanceSummary(BaseModel):
    action: HealthActionSummary
    created: bool


class HealthActionOutcomeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=1, max_length=outcomes_domain.NOTE_MAX_LENGTH)
    observed_at: datetime

    @field_validator("note", mode="before")
    @classmethod
    def _normalize_note(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("note must be a string")
        return outcomes_domain.normalize_note(value)

    @field_validator("observed_at")
    @classmethod
    def _normalize_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must include timezone information")
        normalized = value.astimezone(UTC)
        if normalized > datetime.now(UTC) + outcomes_domain.OBSERVED_AT_MAX_FUTURE_SKEW:
            raise ValueError("observed_at cannot be more than five minutes in the future")
        return normalized


class HealthActionOutcomeSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action_id: uuid.UUID
    note: str
    observed_at: datetime
    created_at: datetime


class DailyAttentionItemSummary(BaseModel):
    kind: str
    title: str
    rationale: str
    evidence_ids: list[uuid.UUID]
    confidence: Literal["low", "medium", "high"]
    limitations: str
    rule_version: str


class InsightEvidenceSummary(BaseModel):
    source_kind: Literal["metric", "symptom", "report_observation"]
    source_record_id: uuid.UUID
    occurred_at: datetime
    role: str | None = None
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


class InsightSummary(BaseModel):
    id: uuid.UUID
    insight_type: Literal["metric_change", "symptom_pattern", "report_observation_update"]
    headline: str
    observed_at: datetime
    evidence: list[InsightEvidenceSummary]


class AssistantTodaySummary(BaseModel):
    generated_at: datetime
    lookback_days: int
    latest_metric: HealthMetricSummary | None
    recent_symptoms: list[SymptomLogSummary]
    open_or_recent_actions: list[HealthActionSummary]
    recent_outcomes: list[HealthActionOutcomeSummary]
    daily_attention: list[DailyAttentionItemSummary]
    insights: list[InsightSummary]


class HistorySourceSummary(BaseModel):
    type: Literal["symptom", "metric", "report_observation"]
    id: uuid.UUID
    report_id: uuid.UUID | None = None
    report_source_name: str | None = None


class HealthHistoryItemSummary(BaseModel):
    id: uuid.UUID
    kind: Literal["symptom", "metric", "report_observation"]
    occurred_at: datetime
    title: str
    primary_value: str | None
    unit: str | None
    detail: str | None
    source: HistorySourceSummary


class HealthReportObservationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    person_id: uuid.UUID
    code: str
    display_name: str
    value_numeric: JsonDecimal | None
    value_text: str | None
    unit: str | None
    reference_range: str | None
    observed_at: datetime
    created_at: datetime


class HealthReportSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    schema_version: str
    source_name: str
    reported_at: datetime
    canonical_sha256: str
    status: str
    created_at: datetime
    confirmed_at: datetime | None


class HealthReportDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    person_id: uuid.UUID
    schema_version: str
    source_name: str
    reported_at: datetime
    canonical_sha256: str
    status: str
    created_at: datetime
    confirmed_at: datetime | None
    observations: list[HealthReportObservationSummary]
