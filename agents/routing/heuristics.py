from __future__ import annotations

TIERS = ("economy", "standard", "high")

SIGNALS = (
    "PHI",
    "HIPAA",
    "PII",
    "SOC2",
    "PCI",
    "multi-agent",
    "multi-system",
    "from scratch",
    "greenfield",
    "p99",
    "SLO",
    "latency budget",
)


def score_tier(ask: str, force: str | None = None) -> tuple[str, str]:
    if force:
        forced = force.strip().lower()
        if forced not in TIERS:
            raise ValueError(f"invalid tier force: {force}")
        return forced, f"forced: {forced}"
    haystack = ask.lower()
    hits = [signal for signal in SIGNALS if signal.lower() in haystack]
    if hits:
        return "high", "matched: " + ", ".join(hits)
    return "standard", "default"
