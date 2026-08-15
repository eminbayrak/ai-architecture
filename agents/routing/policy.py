from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from agents.routing.heuristics import TIERS, score_tier

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")
ROLES = ("intake", "research", "eval", "delivery")
_OVERRIDE_ENV = {
    "intake": "FDE_MODEL_INTAKE",
    "research": "FDE_MODEL_RESEARCH",
    "eval": "FDE_MODEL_EVAL",
    "delivery": "FDE_MODEL_DELIVERY",
}


@dataclass(frozen=True)
class Binding:
    role: str
    model: str
    tier: str
    reason: str


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict):
        raise ValueError("catalog.yaml must have a roles mapping")
    for role in ROLES:
        entry = roles.get(role)
        if not isinstance(entry, dict):
            raise ValueError(f"catalog missing role: {role}")
        models = entry.get("models")
        if not isinstance(models, dict):
            raise ValueError(f"catalog role {role} missing models")
        for tier in TIERS:
            if not models.get(tier):
                raise ValueError(f"catalog role {role} missing model for tier {tier}")
    return roles


def resolve(
    role: str,
    ask: str,
    *,
    environ: Mapping[str, str] | None = None,
    catalog: dict | None = None,
) -> Binding:
    env = os.environ if environ is None else environ
    roles = catalog if catalog is not None else load_catalog()
    if role not in roles:
        raise ValueError(f"unknown role: {role}")
    force = env.get("FDE_TIER_FORCE") or None
    tier, reason = score_tier(ask, force)
    models = roles[role]["models"]
    model = models[tier]
    override_key = _OVERRIDE_ENV.get(role)
    override = env.get(override_key, "").strip() if override_key else ""
    if override:
        model = override
        reason = f"{reason}; override"
    return Binding(role=role, model=model, tier=tier, reason=reason)


def resolve_all(
    ask: str,
    *,
    environ: Mapping[str, str] | None = None,
    catalog: dict | None = None,
) -> dict[str, Binding]:
    roles = catalog if catalog is not None else load_catalog()
    return {
        role: resolve(role, ask, environ=environ, catalog=roles) for role in ROLES
    }
