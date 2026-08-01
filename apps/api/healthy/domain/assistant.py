from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from healthy.domain.actions import HealthActionStatus

RULE_VERSION = "assistant-today-v1"
DEFAULT_LOOKBACK = timedelta(days=14)
EVIDENCE_CAP = 5

Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    id: uuid.UUID
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class SymptomSnapshot:
    id: uuid.UUID
    symptom: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ActionSnapshot:
    id: uuid.UUID
    title: str
    status: HealthActionStatus
    due_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class OutcomeSnapshot:
    id: uuid.UUID
    action_id: uuid.UUID
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class DailyAttentionItem:
    kind: str
    title: str
    rationale: str
    evidence_ids: tuple[uuid.UUID, ...]
    confidence: Confidence
    limitations: str
    rule_version: str


def _due_sort_key(action: ActionSnapshot) -> tuple[datetime, uuid.UUID]:
    assert action.due_at is not None
    return (action.due_at, action.id)


def _describe_open_actions(
    open_actions: list[ActionSnapshot],
    now: datetime,
) -> tuple[str, Confidence, tuple[ActionSnapshot, ...]]:
    with_due = [action for action in open_actions if action.due_at is not None]
    overdue = [action for action in with_due if action.due_at is not None and action.due_at <= now]

    if overdue:
        overdue.sort(key=_due_sort_key)
        earliest = overdue[0]
        assert earliest.due_at is not None
        rationale = (
            f"{len(overdue)} of {len(open_actions)} open action(s) are overdue, "
            f"including '{earliest.title}' (due {earliest.due_at.isoformat()})."
        )
        return rationale, "high", tuple(overdue)

    if with_due:
        with_due.sort(key=_due_sort_key)
        soonest = with_due[0]
        assert soonest.due_at is not None
        rationale = (
            f"{len(open_actions)} action(s) open; next due "
            f"{soonest.due_at.isoformat()} ('{soonest.title}')."
        )
        return rationale, "medium", tuple(open_actions)

    rationale = f"{len(open_actions)} action(s) open with no due date set."
    return rationale, "medium", tuple(open_actions)


def evaluate_daily_attention(
    *,
    now: datetime,
    lookback: timedelta,
    all_time_metric_count: int,
    all_time_symptom_count: int,
    all_time_action_count: int,
    all_time_outcome_count: int,
    recent_metrics: list[MetricSnapshot],
    recent_symptoms: list[SymptomSnapshot],
    open_actions: list[ActionSnapshot],
    recent_outcomes: list[OutcomeSnapshot],
) -> tuple[DailyAttentionItem, ...]:
    """Pure, deterministic Daily Attention Guidance over already-loaded Person records."""
    if (
        all_time_metric_count == 0
        and all_time_symptom_count == 0
        and all_time_action_count == 0
        and all_time_outcome_count == 0
    ):
        return (
            DailyAttentionItem(
                kind="insufficient_data",
                title="Not enough data yet",
                rationale=(
                    "No health metrics, symptoms, actions, or outcomes have been "
                    "recorded for this Person."
                ),
                evidence_ids=(),
                confidence="low",
                limitations="Guidance cannot be generated until at least one record exists.",
                rule_version=RULE_VERSION,
            ),
        )

    lookback_days = lookback.days
    items: list[DailyAttentionItem] = []

    if recent_symptoms:
        most_recent = recent_symptoms[0]
        items.append(
            DailyAttentionItem(
                kind="symptom_recently_reported",
                title="Recent symptom activity",
                rationale=(
                    f"{len(recent_symptoms)} symptom(s) logged in the last {lookback_days} "
                    f"day(s); most recent: '{most_recent.symptom}' on "
                    f"{most_recent.occurred_at.isoformat()}."
                ),
                evidence_ids=tuple(s.id for s in recent_symptoms[:EVIDENCE_CAP]),
                confidence="medium",
                limitations="Self-reported entries only; not a clinical assessment.",
                rule_version=RULE_VERSION,
            )
        )

    if not recent_metrics:
        items.append(
            DailyAttentionItem(
                kind="no_recent_metric",
                title="No recent measurements",
                rationale=f"No health metrics recorded in the last {lookback_days} day(s).",
                evidence_ids=(),
                confidence="high",
                limitations="Reflects data completeness, not health status.",
                rule_version=RULE_VERSION,
            )
        )

    if open_actions:
        rationale, confidence, evidence_source = _describe_open_actions(open_actions, now)
        items.append(
            DailyAttentionItem(
                kind="action_open_or_due",
                title="Open actions need attention",
                rationale=rationale,
                evidence_ids=tuple(a.id for a in evidence_source[:EVIDENCE_CAP]),
                confidence=confidence,
                limitations="Reflects action status only; not a recommendation to act.",
                rule_version=RULE_VERSION,
            )
        )

    if recent_outcomes:
        items.append(
            DailyAttentionItem(
                kind="outcome_recorded",
                title="Outcome recorded for a completed action",
                rationale=(
                    f"{len(recent_outcomes)} outcome(s) recorded in the last "
                    f"{lookback_days} day(s)."
                ),
                evidence_ids=tuple(o.id for o in recent_outcomes[:EVIDENCE_CAP]),
                confidence="medium",
                limitations=(
                    "Outcome notes are free text; this only reports that an outcome was "
                    "recorded, not whether the action helped."
                ),
                rule_version=RULE_VERSION,
            )
        )

    return tuple(items)
