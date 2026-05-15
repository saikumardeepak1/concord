"""PII detection at intake (ADR-009).

Regex-based detector for the common categories. Returns tagged spans and a
redacted version. PII never gets logged or sent to the model verbatim: the
redacted text is what flows through retrieval and routing; the original is
preserved only on the conversation record (which is access-controlled).

This is deliberately a regex pass, not a model call. PII detection runs on every
request including ones we ultimately reject as gibberish, so the cost of a model
call here is not justifiable. A regex with reasonable patterns catches the high-
value categories. Real production would layer in Presidio or a similar library
for higher recall.
"""

from __future__ import annotations

import hashlib
import re

from concord.models import PIITag, PIIType

_PATTERNS: list[tuple[PIIType, re.Pattern[str]]] = [
    # Credit cards must come before generic digit sequences.
    (PIIType.CREDIT_CARD, re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    (PIIType.SSN, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (PIIType.EMAIL, re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (
        PIIType.PHONE,
        re.compile(
            r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)"
        ),
    ),
    (PIIType.IP_ADDRESS, re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    (PIIType.ACCOUNT_ID, re.compile(r"\bacct[_-]?[A-Z0-9]{6,}\b", re.IGNORECASE)),
]


_REDACTION_LABEL = {
    PIIType.EMAIL: "[REDACTED_EMAIL]",
    PIIType.PHONE: "[REDACTED_PHONE]",
    PIIType.CREDIT_CARD: "[REDACTED_CARD]",
    PIIType.SSN: "[REDACTED_SSN]",
    PIIType.ACCOUNT_ID: "[REDACTED_ACCT]",
    PIIType.IP_ADDRESS: "[REDACTED_IP]",
}


def _hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _luhn_valid(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def detect_pii(text: str) -> list[PIITag]:
    """Return tagged spans without modifying text. Spans may overlap; that's fine."""
    tags: list[PIITag] = []
    seen_spans: set[tuple[int, int]] = set()
    for pii_type, pat in _PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if span in seen_spans:
                continue
            raw = m.group(0)
            if pii_type is PIIType.CREDIT_CARD and not _luhn_valid(raw):
                continue
            seen_spans.add(span)
            tags.append(PIITag(type=pii_type, span=span, value_hash=_hash_value(raw)))
    return tags


def redact(text: str, tags: list[PIITag]) -> str:
    """Apply redaction tokens. Stable: redact from right to left to keep spans valid."""
    if not tags:
        return text
    ordered = sorted(tags, key=lambda t: t.span[0], reverse=True)
    out = text
    for tag in ordered:
        start, end = tag.span
        out = out[:start] + _REDACTION_LABEL[tag.type] + out[end:]
    return out
