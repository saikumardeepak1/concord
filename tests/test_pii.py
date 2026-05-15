from concord.intake.pii import detect_pii, redact


def test_detects_email() -> None:
    tags = detect_pii("Contact me at jane.doe@example.com please.")
    assert any(t.type.value == "email" for t in tags)


def test_redacts_email_stably() -> None:
    text = "Email jane@example.com or call 415-555-0123."
    tags = detect_pii(text)
    redacted = redact(text, tags)
    assert "jane@example.com" not in redacted
    assert "415-555-0123" not in redacted
    assert "[REDACTED_EMAIL]" in redacted


def test_credit_card_only_luhn_valid() -> None:
    # 4111 1111 1111 1111 is a known Luhn-valid test PAN.
    valid = "card 4111 1111 1111 1111 charged twice"
    invalid = "code 1234 5678 9012 3456"
    assert any(t.type.value == "credit_card" for t in detect_pii(valid))
    assert not any(t.type.value == "credit_card" for t in detect_pii(invalid))


def test_no_overlap_breakage() -> None:
    # An email in the same line as a phone number must redact both.
    text = "jane@example.com phone 415-555-0123"
    redacted = redact(text, detect_pii(text))
    assert "jane@example.com" not in redacted
    assert "415-555-0123" not in redacted
