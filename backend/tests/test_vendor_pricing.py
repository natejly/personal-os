"""Anthropic and OpenAI list prices: cache read/write billing, embeddings, override rank, tier defaults."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os import providers  # noqa: E402
from personal_os.usage import Pricing  # noqa: E402

M = 1_000_000


def close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) < 1e-9


def test_anthropic_cache_read_and_write() -> None:
    # 1M prompt: 200k cached, 300k written, 500k plain; 100k output.
    got = Pricing().cost({}, "claude-sonnet-5-5", M, 100_000, cached_tokens=200_000, cache_write_tokens=300_000)
    assert close(got, 0.5 * 2.00 + 0.2 * 0.10 + 0.3 * 2.50 + 0.1 * 10.00)


def test_openai_cached_tokens_and_dotted_id() -> None:
    p = Pricing()
    assert close(p.cost({}, "gpt-6.1-sol", M, 0, cached_tokens=M // 2), 0.5 * 2.00 + 0.5 * 0.10)  # the '.' is not rewritten to 'p'
    assert close(p.cost({}, "openai/gpt-6-luna", M, M), 0.10 + 0.50)


def test_embedding_input_only() -> None:
    assert close(Pricing().cost({}, "text-embedding-3-large", 2 * M, 0), 0.26)


def test_override_beats_list_price() -> None:
    p = Pricing()
    s = {"modelPrices": {"gpt-6.1-sol": {"input": 1.0, "output": 1.0}}}
    assert close(p.cost(s, "gpt-6.1-sol", M, M), 2.0)
    assert p.table(s)["gpt-6.1-sol"]["source"] == "override"
    assert p.table({})["claude-opus-5-5"]["source"] == "anthropic"


def test_tier_defaults() -> None:
    for pid, want in (("anthropic", ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")), ("openai", ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"))):
        got = tuple(providers.tier_model({"provider": pid}, t) for t in ("high", "medium", "low"))
        assert got == want, pid
