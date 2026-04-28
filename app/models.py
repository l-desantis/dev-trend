from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Niche(Base):
    __tablename__ = "niches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    keywords_json: Mapped[Any | None] = mapped_column(JSON)

    source_items: Mapped[list["SourceItem"]] = relationship(back_populates="niche")
    signals: Mapped[list["NicheSignal"]] = relationship(back_populates="niche")
    score_history: Mapped[list["NicheScoreHistory"]] = relationship(back_populates="niche")
    briefs: Mapped[list["OpportunityBrief"]] = relationship(back_populates="niche")

    def __repr__(self) -> str:
        return f"<Niche slug={self.slug!r}>"


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
    niche_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("niches.id"), index=True)
    metadata_json: Mapped[Any | None] = mapped_column(JSON)

    niche: Mapped["Niche | None"] = relationship(back_populates="source_items")

    __table_args__ = (
        UniqueConstraint("source_type", "external_id", name="uq_source_items_source_external"),
    )

    def __repr__(self) -> str:
        return f"<SourceItem source={self.source_type!r} id={self.external_id!r}>"


class NicheSignal(Base):
    __tablename__ = "niche_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    niche_id: Mapped[int] = mapped_column(Integer, ForeignKey("niches.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    metric_timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    metadata_json: Mapped[Any | None] = mapped_column(JSON)

    niche: Mapped["Niche"] = relationship(back_populates="signals")

    def __repr__(self) -> str:
        return f"<NicheSignal niche_id={self.niche_id} metric={self.metric_name!r}>"


class NicheScoreHistory(Base):
    __tablename__ = "niche_score_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    niche_id: Mapped[int] = mapped_column(Integer, ForeignKey("niches.id"), nullable=False, index=True)
    score_total: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown_json: Mapped[Any | None] = mapped_column(JSON)
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    niche: Mapped["Niche"] = relationship(back_populates="score_history")

    def __repr__(self) -> str:
        return f"<NicheScoreHistory niche_id={self.niche_id} score={self.score_total}>"


class OpportunityBrief(Base):
    __tablename__ = "opportunity_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    niche_id: Mapped[int] = mapped_column(Integer, ForeignKey("niches.id"), nullable=False, index=True)
    headline: Mapped[str | None] = mapped_column(String(500))
    summary: Mapped[str | None] = mapped_column(Text)
    score_total: Mapped[float | None] = mapped_column(Float)
    score_breakdown_json: Mapped[Any | None] = mapped_column(JSON)
    evidence_json: Mapped[Any | None] = mapped_column(JSON)
    forecast_label: Mapped[str | None] = mapped_column(String(50))
    has_issues: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    model_name: Mapped[str | None] = mapped_column(String(100))

    niche: Mapped["Niche"] = relationship(back_populates="briefs")

    def __repr__(self) -> str:
        return f"<OpportunityBrief niche_id={self.niche_id} score={self.score_total}>"


class MaintenanceState(Base):
    """One-row table tracking maintenance job state (e.g. last pruning run)."""
    __tablename__ = "maintenance_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_pruned_at: Mapped[datetime | None] = mapped_column(DateTime)

    def __repr__(self) -> str:
        return f"<MaintenanceState last_pruned_at={self.last_pruned_at!r}>"
