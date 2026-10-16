"""Typed API contract of the Knowledge Brain. Tasks 2 and 3 depend only on these shapes."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class QueryStatus(str, Enum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    context: Optional[str] = Field(default=None, max_length=4000, description="Optional extra context, e.g. the customer's email.")
    top_k: int = Field(default=5, ge=1, le=10)
    create_escalation: bool = Field(default=True, description="Open a human-expert task when evidence is insufficient.")


class Citation(BaseModel):
    chunk_id: str
    url: str
    title: str
    section: str
    score: float = Field(description="Hybrid retrieval score, 0..1. A ranking signal, NOT a calibrated probability.")
    excerpt: str


class ImageRef(BaseModel):
    url: str
    description: Optional[str] = None
    uncertain: bool = Field(default=True, description="True when the description was not verified by a vision model.")
    chunk_id: str


class QueryResponse(BaseModel):
    request_id: str
    status: QueryStatus
    answer: Optional[str] = None
    citations: list[Citation] = []
    images: list[ImageRef] = []
    evidence_score: float = Field(description="Best retrieval score; see Citation.score for semantics.")
    reason: Optional[str] = Field(default=None, description="Why the status is insufficient_evidence.")
    escalation_id: Optional[int] = None
    answered_by: str = Field(description="'llm:<model>' or 'offline-extractive'.")
    expert_record_ids: list[int] = []


class IngestionRunRequest(BaseModel):
    limit: Optional[int] = Field(default=None, ge=1, description="Process at most N pages (use 10-20 for a first run).")
    url_filter: Optional[str] = Field(default=None, description="Only URLs containing this substring.")


class IngestionReport(BaseModel):
    job_id: int
    state: str
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    failed: int = 0
    started_at: str
    finished_at: Optional[str] = None


class EscalationCreate(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    reason: str = "manual"


class EscalationResolve(BaseModel):
    answer: str = Field(min_length=3, max_length=8000)
    evidence_urls: list[str] = []
    reviewer: str
    approve_for_retrieval: bool = False
    expires_at: Optional[str] = Field(default=None, description="ISO date after which the record stops being retrieved.")


class Escalation(BaseModel):
    id: int
    question: str
    reason: str
    status: str
    evidence: list[dict] = []
    created_at: str
    resolved_at: Optional[str] = None


class Health(BaseModel):
    status: str
    llm: str
    active_pages: int
    active_chunks: int
    open_escalations: int
