from app.tokens import build_token_budget, count_exact, count_tokens


def test_count_tokens_is_positive_and_openai_is_exact():
    assert count_tokens("hola mundo") > 0
    assert count_tokens("") == 0
    assert count_exact("openrouter/openai/gpt-4o-mini")
    assert not count_exact("openrouter/anthropic/claude-haiku-4.5")


def test_budget_fits_and_rejects():
    ok = build_token_budget(
        instructions="instrucciones",
        context="contexto",
        transcription="reunión",
        history="",
        reserved_output=100,
        safety_margin=10,
        window=10_000,
        model="gpt-4o-mini",
        cacheable="instrucciones",
    )
    assert ok.fits
    assert ok.exact
    assert ok.per_thousand_calls == ok.total_input * 1000

    tight = build_token_budget(
        instructions="x" * 400,
        context="y" * 400,
        transcription="z" * 400,
        history="",
        reserved_output=1000,
        safety_margin=1000,
        window=50,
        model="claude",
        cacheable="x",
    )
    assert not tight.fits
    assert not tight.exact
