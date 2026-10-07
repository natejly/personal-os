"""Fireworks list prices: id prefixes, proxy aliases, cached input, embeddings, unknown models (never $0)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.usage import FIREWORKS_PRICES, Pricing  # noqa: E402

M = 1_000_000


def close(a: float | None, b: float) -> bool:
    return a is not None and abs(a - b) < 1e-9


def test_full_id_and_short_name_price_the_same() -> None:
    p = Pricing()
    for model in ("accounts/fireworks/models/ember-1", "ember-1", "fireworks_ai/accounts/fireworks/models/ember-1"):
        assert close(p.cost({}, model, M, M), 3.00 + 15.00), model
    assert close(p.cost({}, "accounts/fireworks/models/deepseek-v4p1-flash", M, M), 0.30 + 1.20)  # 2026-10-01 price, not 0.22/0.66
    assert close(p.cost({}, "accounts/fireworks/models/glm-5p3-flash", M, M), 0.15 + 0.50)


def test_cached_tokens_billed_once_at_cached_rate() -> None:
    p = Pricing()
    # 1M prompt tokens of which 600k cached: 400k at 1.40 + 600k at 0.26, plus 100k output at 4.40.
    got = p.cost({}, "accounts/fireworks/models/glm-5p3", M, 100_000, cached_tokens=600_000)
    assert close(got, 0.4 * 1.40 + 0.6 * 0.26 + 0.1 * 4.40)
    # More cached than prompt (a bad usage object) never bills beyond the prompt.
    assert close(p.cost({}, "kimi-k3", M, 0, cached_tokens=5 * M), 0.30)


def test_embeddings_input_only() -> None:
    p = Pricing()
    assert close(p.cost({}, "accounts/fireworks/models/qwen3-embedding-8b", 2 * M, 0), 0.20)


def test_unknown_model_is_none_not_zero() -> None:
    p = Pricing()
    assert p.cost({}, "accounts/fireworks/models/minimax-m3", M, M) is None
    assert p.cost({}, "accounts/fireworks/models/deepseek-v4-pro", 10, 10) is None
    assert p.cost({}, "", 10, 10) is None


def test_proxy_alias_uses_list_price_over_stale_proxy_price() -> None:
    p = Pricing()
    p._proxy = {"deepseek-v4-flash": {"input": 0.22, "output": 0.66, "cache_read": 0.007}, "minimax-m3": {"input": 0.5, "output": 1.0}}
    p._targets = {"deepseek-v4-flash": "fireworks_ai/accounts/fireworks/models/deepseek-v4p1-flash", "glm-5.3": "fireworks_ai/accounts/fireworks/models/glm-5p3"}
    table = p.table({})
    assert table["deepseek-v4-flash"]["source"] == "fireworks"
    assert close(p.cost({}, "deepseek-v4-flash", M, M), 0.30 + 1.20)
    assert close(p.cost({}, "glm-5.3", M, 0, cached_tokens=M), 0.26)
    assert close(p.cost({}, "minimax-m3", M, M), 1.5)  # a model only the proxy prices keeps that price


def test_override_wins_and_keeps_cached_rate() -> None:
    p = Pricing()
    cfg = {"modelPrices": {"ember-1": {"input": 2.0, "output": 10.0}}}
    assert p.table(cfg)["ember-1"]["source"] == "override"
    assert close(p.cost(cfg, "accounts/fireworks/models/ember-1", M, M, cached_tokens=M // 2), 0.5 * 2.0 + 0.5 * 0.30 + 10.0)


def test_half_priced_override_is_unknown_for_the_missing_side() -> None:
    p = Pricing()
    cfg = {"modelPrices": {"new-model": {"input": 1.0}}}
    assert close(p.cost(cfg, "new-model", M, 0), 1.0)
    assert p.cost(cfg, "new-model", M, 10) is None


def test_table_is_per_million() -> None:
    assert FIREWORKS_PRICES["kimi-k3"] == {"input": 3.00, "cache_read": 0.30, "output": 15.00}


def test_override_under_full_id_beats_list_price_by_short_name() -> None:
    cfg = {"modelPrices": {"accounts/fireworks/models/ember-1": {"input": 1.0, "output": 0.0}}}
    assert close(Pricing().cost(cfg, "ember-1", M, 0), 1.0)
