from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    source_items: Mapped[list["SourceItem"]] = relationship(back_populates="category")
    candidates: Mapped[list["OpportunityCandidate"]] = relationship(back_populates="category")

    def __repr__(self) -> str:
        return f"<Category slug={self.slug!r}>"


class SourceItem(Base):
    __tablename__ = "source_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime | None] = mapped_column(DateTime)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    metadata_json: Mapped[Any | None] = mapped_column(JSON)

    # v4 fields
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), index=True, nullable=True
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="extraction", index=True
    )
    extraction_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    category: Mapped["Category | None"] = relationship(back_populates="source_items")
    pain_points: Mapped[list["PainPoint"]] = relationship(
        back_populates="source_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_source_items_source_external"),
    )

    def __repr__(self) -> str:
        return f"<SourceItem source={self.source_type!r} id={self.external_id!r}>"


class PainPoint(Base):
    __tablename__ = "pain_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=True, index=True
    )
    extractor_model: Mapped[str] = mapped_column(String(100), nullable=False)
    problem_text: Mapped[str | None] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(String(300))
    urgency_cue: Mapped[str | None] = mapped_column(String(200))
    current_workaround: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))  # list[float]
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True
    )

    source_item: Mapped["SourceItem"] = relationship(back_populates="pain_points")
    candidate: Mapped["OpportunityCandidate | None"] = relationship(back_populates="pain_points")

    def __repr__(self) -> str:
        return f"<PainPoint source_item_id={self.source_item_id} model={self.extractor_model!r}>"


class OpportunityCandidate(Base):
    __tablename__ = "opportunity_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("categories.id"), nullable=True, index=True
    )
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audience: Mapped[str | None] = mapped_column(String(300))
    why_now: Mapped[str | None] = mapped_column(Text)
    specificity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifecycle_state: Mapped[str | None] = mapped_column(String(50), index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    # NULL = unlabelled sentinel; set to model name after labelling
    labeller_model: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    centroid: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))  # list[float]
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    category: Mapped["Category | None"] = relationship(back_populates="candidates")
    pain_points: Mapped[list["PainPoint"]] = relationship(back_populates="candidate")
    validations: Mapped[list["CandidateValidation"]] = relationship(back_populates="candidate")
    score_history: Mapped[list["CandidateScoreHistory"]] = relationship(back_populates="candidate")
    briefs: Mapped[list["CandidateBrief"]] = relationship(back_populates="candidate")
    feedback: Mapped[list["CandidateFeedback"]] = relationship(back_populates="candidate")

    def __repr__(self) -> str:
        return f"<OpportunityCandidate id={self.id} specificity={self.specificity}>"


class CandidateValidation(Base):
    __tablename__ = "candidate_validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=False, index=True
    )
    source_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("source_items.id"), nullable=True
    )
    signal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_value: Mapped[float | None] = mapped_column(Float)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    metadata_json: Mapped[Any | None] = mapped_column(JSON)

    candidate: Mapped["OpportunityCandidate"] = relationship(back_populates="validations")

    def __repr__(self) -> str:
        return f"<CandidateValidation candidate_id={self.candidate_id} type={self.signal_type!r}>"


class CandidateScoreHistory(Base):
    __tablename__ = "candidate_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=False, index=True
    )
    score_total: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown_json: Mapped[Any | None] = mapped_column(JSON)
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    candidate: Mapped["OpportunityCandidate"] = relationship(back_populates="score_history")

    def __repr__(self) -> str:
        return f"<CandidateScoreHistory candidate_id={self.candidate_id} score={self.score_total}>"


class CandidateBrief(Base):
    __tablename__ = "candidate_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=False, index=True
    )
    headline: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    model_name: Mapped[str | None] = mapped_column(String(100))
    evidence_json: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))

    candidate: Mapped["OpportunityCandidate"] = relationship(back_populates="briefs")

    def __repr__(self) -> str:
        return f"<CandidateBrief candidate_id={self.candidate_id}>"


class CandidateFeedback(Base):
    __tablename__ = "candidate_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    brief_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("candidate_briefs.id"), nullable=True
    )
    label: Mapped[str] = mapped_column(String(10), nullable=False)  # 'up' | 'down'
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )

    candidate: Mapped["OpportunityCandidate"] = relationship(back_populates="feedback")

    # SQLite: NULLs are distinct in UNIQUE, so this constraint handles non-null brief_id uniqueness.
    __table_args__ = (
        UniqueConstraint("candidate_id", "user_id", "brief_id", name="uq_candidate_feedback"),
    )

    def __repr__(self) -> str:
        return f"<CandidateFeedback candidate_id={self.candidate_id} user={self.user_id!r}>"


class LifecycleEvent(Base):
    __tablename__ = "lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("opportunity_candidates.id"), nullable=False, index=True
    )
    old_state: Mapped[str | None] = mapped_column(String(50))
    new_state: Mapped[str] = mapped_column(String(50), nullable=False)
    score_total: Mapped[float | None] = mapped_column(Float)
    was_alerted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(UTC), index=True
    )

    def __repr__(self) -> str:
        return f"<LifecycleEvent candidate_id={self.candidate_id} {self.old_state!r}→{self.new_state!r}>"


class MaintenanceState(Base):
    """One-row table tracking maintenance job state (e.g. last pruning run)."""
    __tablename__ = "maintenance_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_pruned_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<MaintenanceState last_pruned_at={self.last_pruned_at!r}>"
