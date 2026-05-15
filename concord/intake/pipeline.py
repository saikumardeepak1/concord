"""Intake stage: the single entry-point for raw inbound text.

Produces an `IntakeResult` that the router and specialists consume. The redacted
text is what every downstream stage sees by default. The original survives only
on the conversation record.
"""

from __future__ import annotations

from concord.config import get_settings
from concord.intake.normalize import detect_language, is_likely_gibberish, normalize
from concord.intake.pii import detect_pii, redact
from concord.intake.summarize import summarize_history
from concord.models import IntakeResult
from concord.observability.tracing import span


class GibberishInputError(ValueError):
    """Raised when the inbound message is too short or non-textual to handle."""


class IntakeStage:
    """Orchestrates normalization, PII handling, and (optional) thread summarization."""

    async def process(
        self,
        *,
        raw_message: str,
        history: list[dict[str, str]] | None = None,
    ) -> IntakeResult:
        settings = get_settings()
        async with span("intake.process", raw_len=len(raw_message or "")) as s:
            normalized = normalize(raw_message or "")
            if is_likely_gibberish(normalized):
                raise GibberishInputError("input too short or non-textual")

            tags = detect_pii(normalized) if settings.pii_redaction_enabled else []
            redacted = redact(normalized, tags) if tags else normalized
            language = detect_language(normalized)

            summary = None
            kept = history or []
            if history:
                summary_text, kept = await summarize_history(history)
                if summary_text:
                    summary = summary_text

            s.attributes.update(
                pii_tag_count=len(tags),
                language=language,
                summarized=summary is not None,
                kept_history=len(kept),
            )
            return IntakeResult(
                normalized_text=normalized,
                redacted_text=redacted,
                pii_tags=tags,
                thread_summary=summary,
                original_thread_message_count=len(history or []),
                language=language,
            )
