"""Structured records for the Meeting-to-Scope pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Role(str, Enum):
    CLIENT = "client"
    CONSULTANT = "consultant"
    OTHER = "other"


class Turn(BaseModel):
    id: int
    ts: str
    speaker: str
    role: Role
    text: str


class SourceRef(BaseModel):
    meeting_id: int
    turn_id: int
    ts: str
    speaker: str
    quote: str


class Status(str, Enum):
    CONFIRMED = "confirmed"
    UNCLEAR = "unclear"
    DISPUTED = "disputed"


class Priority(str, Enum):
    MUST = "must-have"
    SHOULD = "should-have"
    OPTIONAL = "optional"


class Intent(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    REMOVE = "remove"


class Feasibility(str, Enum):
    EXISTING = "existing_configuration"
    LIMITED = "existing_with_limitation"
    CUSTOM = "potential_custom_development"
    CLARIFY = "needs_clarification"
    UNCHECKED = "not_checked"


class FeasibilityResult(BaseModel):
    category: Feasibility = Feasibility.UNCHECKED
    rationale: str = ""
    evidence: list[dict] = []          # [{url, title, excerpt}]
    verified: bool = False             # True only when the Knowledge Brain returned supporting evidence
    needs_expert: bool = False
    kb_error: Optional[str] = None


class Requirement(BaseModel):
    id: str = ""                        # REQ-001, assigned when added to a project; stable across versions
    topic: str
    description: str
    sources: list[SourceRef] = []
    status: Status = Status.CONFIRMED
    priority: Priority = Priority.SHOULD
    origin: str = "explicit"            # explicit (client said it) | inferred (agent deduced it)
    intent: Intent = Intent.ADD         # relative to the baseline, only meaningful for follow-ups
    existing_solution: FeasibilityResult = Field(default_factory=FeasibilityResult)
    acceptance_criteria: list[str] = []
    dependencies: list[str] = []
    estimate_status: str = "not estimated"   # not estimated | estimated | needs review
    in_scope: bool = True               # False = flagged for a separate quotation (not part of the approved baseline theme)
    notes: list[str] = []


class ChunkResult(BaseModel):
    requirements: list[Requirement] = []
    decisions: list[SourceRef] = []
    client_commitments: list[SourceRef] = []
    exclusions: list[SourceRef] = []
    deadlines: list[SourceRef] = []
    open_questions: list[str] = []


class ProjectRepresentation(BaseModel):
    """Project-level view of one meeting after the staged merge."""
    meeting_id: int
    requirements: list[Requirement] = []
    decisions: list[SourceRef] = []
    client_commitments: list[SourceRef] = []
    exclusions: list[SourceRef] = []
    deadline_text: Optional[str] = None
    deadline_iso: Optional[str] = None
    deadline_source: Optional[SourceRef] = None
    open_questions: list[str] = []
    warnings: list[str] = []
    chunks_processed: int = 1


class EstimateTask(BaseModel):
    requirement_id: str
    name: str
    low: float
    likely: float
    high: float
    reasoning: str
    assumptions: list[str] = []
    dependencies: list[str] = []


class Estimate(BaseModel):
    tasks: list[EstimateTask]
    total_low: float
    total_likely: float
    total_high: float
    contingency_hours: float
    contingency_reason: str
    weeks_at_capacity: float
    capacity_hours_per_week: float
    per_requirement: dict[str, dict] = {}   # id -> {low, likely, high, delta_likely (vs baseline)}
    pricing: dict = {}
    notes: list[str] = []


class ChangeReport(BaseModel):
    baseline_version_id: int
    added: list[dict] = []
    modified: list[dict] = []
    removed: list[dict] = []
    unchanged: list[str] = []
    potentially_out_of_scope: list[dict] = []
    unresolved_conflicts: list[dict] = []
    deadline: Optional[dict] = None
    estimate_delta: dict = {}
    summary: str = ""


class VersionStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    client: str = Field(min_length=2, max_length=120)


class MeetingCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    kind: str = Field(pattern=r"^(initial|follow_up)$")
    transcript: str = Field(min_length=40, max_length=400_000)
    chunk_turns: Optional[int] = Field(default=None, ge=4, le=200)


class ApproveRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = ""


class SendRequest(BaseModel):
    to: str = Field(min_length=3, max_length=200)


class EditRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=80)
    task_hours: dict[str, dict] = {}    # "REQ-001|task name" -> {low, likely, high}
    note: str = ""
