# Model routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route each FDE crew node to a model from an architect-defined catalog plus a free complexity gate, and reposition the kit as FDE + AI Architect.

**Architecture:** `agents/routing/` owns policy (`catalog.yaml`, heuristics, `resolve`). The graph accepts a single model (tests) or a per-role map (live). `route_log` records role, model, tier, reason. No LLM call for routing.

**Tech Stack:** Existing kit plus PyYAML.

## Global Constraints

- No extra LLM call to choose a model
- `economy` only via `FDE_TIER_FORCE` (PHI cannot silently go cheap)
- Tests never call a live LLM
- No em-dashes
- No git push until Emin confirms

---

### Task 1: Routing policy (TDD)

**Files:**
- Create: `tests/test_routing.py`
- Create: `agents/routing/__init__.py`
- Create: `agents/routing/catalog.yaml`
- Create: `agents/routing/heuristics.py`
- Create: `agents/routing/policy.py`
- Modify: `pyproject.toml` (add `pyyaml`)

**Interfaces:**
- Produces: `Binding(role, model, tier, reason)`
- Produces: `score_tier(ask: str, force: str | None) -> tuple[str, str]`
- Produces: `resolve(role: str, ask: str, *, environ=os.environ) -> Binding`
- Produces: `resolve_all(ask: str, *, environ=os.environ) -> dict[str, Binding]`

- [ ] **Step 1: Add pyyaml** to `pyproject.toml` dependencies, `uv sync --group dev`.

- [ ] **Step 2: Write failing tests** in `tests/test_routing.py`

```python
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
```

- [ ] **Step 3: Run tests, expect fail** (`uv run pytest tests/test_routing.py -v`).

- [ ] **Step 4: Implement catalog + heuristics + policy**

`catalog.yaml`:

```yaml
roles:
  intake:
    specialty: structured extraction
    models: {economy: gpt-4.1-mini, standard: gpt-4.1-mini, high: gpt-4.1-mini}
  research:
    specialty: scoping / risks
    models: {economy: gpt-4.1-mini, standard: gpt-4.1-mini, high: gpt-4.1-mini}
  eval:
    specialty: architecture + evals
    models: {economy: gpt-4.1-mini, standard: gpt-4.1, high: gpt-4.1}
  delivery:
    specialty: plan + customer writing
    models: {economy: gpt-4.1-mini, standard: gpt-4.1, high: gpt-4.1}
```

`heuristics.py`: case-insensitive substring list from the spec. `score_tier` returns `(tier, reason)`. Invalid `force` raises `ValueError`.

`policy.py`: `@dataclass(frozen=True) class Binding`. Load YAML from the package dir. Fail fast if role or tier missing. Env `FDE_TIER_FORCE` then `FDE_MODEL_{ROLE}`.

- [ ] **Step 5: `uv run pytest tests/test_routing.py -v`** Expected: PASS.

---

### Task 2: Graph uses per-role models + route_log (TDD)

**Files:**
- Modify: `agents/graphs/fde_crew.py`
- Modify: `tests/test_fde_crew.py`

**Interfaces:**
- `CrewState.route_log: list[dict]` with keys role, model, tier, reason
- `build_graph(model=None, models=None, bindings=None)`
- `run_crew(customer_ask, model=None, models=None, bindings=None)` — existing tests pass a single `model`
- `main()` uses `resolve_all` + shared `ChatOpenAI` per model id; prints route_log then sections

- [ ] **Step 1: Extend `tests/test_fde_crew.py`**

```python
def test_route_log_has_four_entries():
    state = run_crew("Acme wants a RAG eval harness", _model())
    assert len(state["route_log"]) == 4
    assert [e["role"] for e in state["route_log"]] == [
        "intake", "research", "eval", "delivery"
    ]
```

- [ ] **Step 2: Run that test, expect fail** (missing `route_log`).

- [ ] **Step 3: Update graph**

`_empty_state` includes `route_log: []`. `_make_node` appends a log dict. If `bindings` is None, log `model=type(model).__name__`, `tier="test"`, `reason="injected"`. If `models` is a dict, use `models[name]`. Keep halt-on-error.

`main`: resolve_all, instantiate ChatOpenAI per distinct model id (share), pass models+bindings, print `[role] model  tier  reason` before sections.

Keep `require_api_key`. Drop sole reliance on `FDE_CREW_MODEL` (per-role env replaces it).

- [ ] **Step 4: `uv run pytest tests/ -v`** Expected: PASS.

---

### Task 3: Prompts, README, env example

**Files:**
- Modify: `agents/prompts/eval.md`
- Modify: `agents/prompts/delivery.md`
- Modify: `README.md`
- Modify: `agents/README.md`
- Modify: `.env.example`

- [ ] **Step 1:** Add a short **AI Architect** subsection to eval (model/tool tradeoffs, eval-before-code) and delivery (architecture choices in the plan). Do not rewrite intake/research.

- [ ] **Step 2:** README: FDE + AI Architect kit. Show the default routing table. Document `FDE_TIER_FORCE` and `FDE_MODEL_*`.

- [ ] **Step 3:** `.env.example` add those vars (empty).

- [ ] **Step 4:** `uv run pytest` PASS. Stop. Show `git status`. No push until Emin says yes.

---

## Spec coverage

| Spec | Task |
|------|------|
| catalog + heuristics + resolve | 1 |
| graph route_log + per-role models + CLI print | 2 |
| prompts + README + env | 3 |
| tests listed in spec | 1, 2 |
