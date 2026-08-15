# Model routing + AI Architect positioning — design spec

Date: 2026-08-13
Status: approved
Repo: `fde-lab` (existing)

## Problem

Every FDE crew node uses one model (`FDE_CREW_MODEL`, default `gpt-4.1-mini`). That is neither cost-efficient nor specialty-aware: intake does not need the same model as eval. The kit also reads as FDE-only, but the work includes AI architecture (routing policy, cost/quality tradeoffs, eval-before-code).

## Goals

1. Architect-defined routing policy: role + complexity tier → model. No extra LLM call to choose.
2. Free heuristic complexity gate. Env can force a tier or override a role's model.
3. CLI prints which model ran on which node and why.
4. README and eval/delivery prompts speak FDE + AI Architect. Repo name stays `fde-lab`. Keep four nodes.

## Non-goals

- Router LLM / LiteLLM / OpenRouter
- Per-token cost accounting or live price APIs
- Adding a fifth architecture node
- Changing the transcribe-video / watch skills

## Architecture

New package `agents/routing/`. The graph does not hardcode model ids.

```
agents/routing/
  catalog.yaml      policy (source of truth)
  heuristics.py     free signals → tier
  policy.py         resolve(role, ask) → Binding
```

`build_graph` accepts a single model (all roles, for tests) or a `dict[str, model]` keyed by role. Live CLI builds the dict from `policy.resolve`.

`CrewState` gains `route_log: list[dict]`. Each entry: `role`, `model`, `tier`, `reason`.

## Catalog

YAML, OpenAI model ids (still works with `OPENAI_BASE_URL`).

| Role | Specialty | economy | standard | high |
|------|-----------|---------|----------|------|
| intake | structured extraction | gpt-4.1-mini | gpt-4.1-mini | gpt-4.1-mini |
| research | scoping / risks | gpt-4.1-mini | gpt-4.1-mini | gpt-4.1-mini |
| eval | architecture + evals | gpt-4.1-mini | gpt-4.1 | gpt-4.1 |
| delivery | plan + customer writing | gpt-4.1-mini | gpt-4.1 | gpt-4.1 |

Env overrides (win over catalog): `FDE_MODEL_INTAKE`, `FDE_MODEL_RESEARCH`, `FDE_MODEL_EVAL`, `FDE_MODEL_DELIVERY`.

`FDE_TIER_FORCE=economy|standard|high` skips heuristics.

## Complexity gate

Default tier is `standard`. Any heuristic hit → `high`. `economy` is opt-in via `FDE_TIER_FORCE` only (so a PHI ask cannot silently go cheap).

Case-insensitive substring match on the customer ask:

- PHI, HIPAA, PII, SOC2, PCI
- multi-agent, multi-system, from scratch, greenfield
- p99, SLO, latency budget

`reason` is `default` or `matched: PHI, multi-system` or `forced: economy`.

## Data flow

1. Load catalog. Fail fast if a role or tier is missing.
2. Score tier from ask (or `FDE_TIER_FORCE`).
3. For each role, `resolve` → Binding (model id, tier, reason). Apply env model override; if overridden, reason includes `override`.
4. Build one `ChatOpenAI` per distinct model id (share instances).
5. Run graph. Each node appends its Binding to `route_log`.
6. CLI prints `[role] model  tier  reason` then the four sections.

Routing never calls an LLM. Node failures still set `error` and stop.

## Prompts and docs

- `eval.md` / `delivery.md`: short AI Architect voice (tradeoffs, model/tool choices, eval-before-code). Still no harness syntax.
- `intake.md` / `research.md`: unchanged FDE shape.
- README: FDE + AI Architect kit; routing table is the exhibit. How to force a tier / override a model.

## Testing

No network.

- `resolve("eval", "add a button")` → standard, gpt-4.1
- `resolve("eval", "PHI RAG")` → high, gpt-4.1
- `FDE_TIER_FORCE=economy` → eval is gpt-4.1-mini
- `FDE_MODEL_EVAL` wins over catalog
- `run_crew` with one FakeListChatModel still fills four fields; `route_log` has four entries
- Existing compile / halt-on-error / missing-key tests still pass
- Unknown role or missing catalog key → raises a clear error

## Dependencies

Add `pyyaml` for catalog load. Keep uv, langgraph, langchain-openai.

## Success criteria

- Easy ask: intake/research on mini, eval/delivery on gpt-4.1
- PHI ask: `route_log` reason shows the match. With this catalog, standard and high use the same model ids; the tier is the extension point for a stronger high model later
- `FDE_TIER_FORCE=economy` runs all four on mini
- `uv run pytest` passes with no network
- README describes FDE + AI Architect routing, not a single-model crew

Cost vs "one strong model for everything": intake and research stay on mini. `FDE_TIER_FORCE=economy` is the kill switch for a cheap full run.
