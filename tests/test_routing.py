import pytest

from agents.routing.policy import resolve, resolve_all


def test_eval_easy_ask_is_standard_gpt41():
    b = resolve("eval", "add a button")
    assert b.tier == "standard"
    assert b.model == "gpt-4.1"
    assert b.reason == "default"


def test_eval_phi_is_high():
    b = resolve("eval", "PHI RAG")
    assert b.tier == "high"
    assert b.model == "gpt-4.1"
    assert "PHI" in b.reason


def test_economy_force_uses_mini(monkeypatch):
    monkeypatch.setenv("FDE_TIER_FORCE", "economy")
    b = resolve("eval", "PHI RAG")
    assert b.model == "gpt-4.1-mini"
    assert "forced: economy" in b.reason


def test_model_override_wins(monkeypatch):
    monkeypatch.setenv("FDE_MODEL_EVAL", "gpt-4.1-mini")
    b = resolve("eval", "add a button")
    assert b.model == "gpt-4.1-mini"
    assert "override" in b.reason


def test_unknown_role_raises():
    with pytest.raises(ValueError, match="unknown role"):
        resolve("cook", "x")


def test_resolve_all_four_roles():
    bindings = resolve_all("add a button")
    assert set(bindings) == {"intake", "research", "eval", "delivery"}
    assert bindings["intake"].model == "gpt-4.1-mini"
    assert bindings["eval"].model == "gpt-4.1"
