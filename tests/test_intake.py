import pytest

from concord.intake.pipeline import GibberishInputError, IntakeStage


async def test_intake_normalizes_and_redacts_pii() -> None:
    stage = IntakeStage()
    result = await stage.process(
        raw_message="My email is jane@example.com please refund me   thanks!!!",
    )
    assert "jane@example.com" not in result.redacted_text
    assert "[REDACTED_EMAIL]" in result.redacted_text
    assert result.language == "en"


async def test_intake_rejects_gibberish() -> None:
    stage = IntakeStage()
    with pytest.raises(GibberishInputError):
        await stage.process(raw_message="...")


async def test_intake_detects_non_english() -> None:
    stage = IntakeStage()
    result = await stage.process(raw_message="こんにちは、サポートが必要です。アカウントにアクセスできません。")
    assert result.language == "non-en"
