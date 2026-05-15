"""Conversation state and audit log persistence.

ADR-007: request handlers stay stateless; all state lives here, in an external
store. The store is SQLite by default (so the demo runs with zero ops), but the
SQLAlchemy async layer means swapping to Postgres is a connection-string change.

Three tables:
- conversations: thread state, the running message history per conversation.
- audit_log: immutable record of every state-changing action.
- traces: persisted request traces for the live trace viewer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from concord.config import get_settings


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    messages_json: Mapped[str] = mapped_column(Text)  # list[Message] serialized
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLogEntry(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    tool_name: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean)
    verification_rationale: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class TraceRecord(Base):
    __tablename__ = "traces"
    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.db_url, future=True, echo=False)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def init_db() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session() -> AsyncSession:
    _get_engine()
    assert _sessionmaker is not None
    return _sessionmaker()


class ConversationStore:
    """High-level conversation persistence."""

    async def load(self, conversation_id: str) -> Conversation | None:
        async with get_session() as session:
            return await session.get(Conversation, conversation_id)

    async def save(
        self,
        *,
        conversation_id: str,
        customer_id: str,
        messages: list[dict[str, Any]],
        summary: str | None = None,
        closed: bool = False,
    ) -> None:
        now = datetime.now(UTC)
        async with get_session() as session:
            existing = await session.get(Conversation, conversation_id)
            if existing is None:
                existing = Conversation(
                    conversation_id=conversation_id,
                    customer_id=customer_id,
                    created_at=now,
                    updated_at=now,
                    messages_json=json.dumps(messages, default=str),
                    summary=summary,
                    closed=closed,
                )
                session.add(existing)
            else:
                existing.updated_at = now
                existing.messages_json = json.dumps(messages, default=str)
                if summary is not None:
                    existing.summary = summary
                existing.closed = closed
            await session.commit()


class AuditLog:
    """Append-only audit log of state-changing actions."""

    async def record(
        self,
        *,
        request_id: str,
        trace_id: str,
        customer_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        approved: bool,
        verification_rationale: str,
        idempotency_key: str | None = None,
    ) -> None:
        async with get_session() as session:
            entry = AuditLogEntry(
                request_id=request_id,
                trace_id=trace_id,
                customer_id=customer_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                approved=approved,
                verification_rationale=verification_rationale,
                occurred_at=datetime.now(UTC),
                idempotency_key=idempotency_key,
            )
            session.add(entry)
            await session.commit()

    async def find_by_idempotency_key(self, key: str) -> AuditLogEntry | None:
        """Return the latest APPROVED entry for this key, or None.

        We restrict to approved entries because idempotency replay should only
        repeat a prior successful run, not replay a denial. Multiple denials
        for the same key are legitimate (e.g. a customer keeps asking, policy
        keeps refusing); each must produce its own audit record.
        """
        async with get_session() as session:
            stmt = (
                select(AuditLogEntry)
                .where(AuditLogEntry.idempotency_key == key)
                .where(AuditLogEntry.approved.is_(True))
                .order_by(AuditLogEntry.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalars().first()


class TraceStore:
    async def save(self, trace_dict: dict[str, Any], outcome: str | None = None) -> None:
        async with get_session() as session:
            rec = TraceRecord(
                trace_id=trace_dict["trace_id"],
                request_id=trace_dict["request_id"],
                customer_id=trace_dict.get("customer_id"),
                started_at=datetime.fromisoformat(trace_dict["started_at"]),
                outcome=outcome,
                payload=trace_dict,
            )
            session.add(rec)
            await session.commit()

    async def get(self, trace_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            rec = await session.get(TraceRecord, trace_id)
            return rec.payload if rec else None

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        async with get_session() as session:
            stmt = select(TraceRecord).order_by(TraceRecord.started_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return [r.payload for r in result.scalars().all()]
