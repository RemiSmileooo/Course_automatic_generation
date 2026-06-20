from src.config import Settings


def test_llm_extra_body_uses_valid_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "Medium")

    settings = Settings()

    assert settings.llm_extra_body() == {"reasoning_effort": "medium"}


def test_llm_extra_body_ignores_invalid_reasoning_effort(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "turbo")

    settings = Settings()

    assert settings.llm_extra_body() == {}
