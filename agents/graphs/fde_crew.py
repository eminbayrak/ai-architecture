from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from agents.routing.policy import Binding, resolve_all

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
NODE_FIELDS = (
    ("intake", "engagement_brief"),
    ("research", "research_brief"),
    ("eval", "eval_plan"),
    ("delivery", "delivery"),
)


class CrewState(TypedDict):
    customer_ask: str
    engagement_brief: str
    research_brief: str
    eval_plan: str
    delivery: str
    error: str
    route_log: list[dict]


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "Missing OPENAI_API_KEY. Copy .env.example to .env and set the key.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _empty_state(customer_ask: str) -> CrewState:
    return {
        "customer_ask": customer_ask,
        "engagement_brief": "",
        "research_brief": "",
        "eval_plan": "",
        "delivery": "",
        "error": "",
        "route_log": [],
    }


def _context(state: CrewState) -> str:
    return (
        f"Customer ask:\n{state['customer_ask']}\n\n"
        f"Engagement brief:\n{state['engagement_brief']}\n\n"
        f"Research brief:\n{state['research_brief']}\n\n"
        f"Eval plan:\n{state['eval_plan']}\n"
    )


def _log_entry(name: str, model: Any, binding: Binding | None) -> dict:
    if binding is not None:
        return {
            "role": binding.role,
            "model": binding.model,
            "tier": binding.tier,
            "reason": binding.reason,
        }
    return {
        "role": name,
        "model": type(model).__name__,
        "tier": "test",
        "reason": "injected",
    }


def _make_node(name: str, field: str, model: Any, binding: Binding | None):
    prompt = load_prompt(name)

    def node(state: CrewState) -> dict:
        if state.get("error"):
            return {}
        entry = _log_entry(name, model, binding)
        log = [*state.get("route_log", []), entry]
        try:
            result = model.invoke(
                [SystemMessage(content=prompt), HumanMessage(content=_context(state))]
            )
            text = result.content if hasattr(result, "content") else str(result)
            return {field: text, "route_log": log}
        except Exception as exc:
            return {"error": f"{name} failed: {exc}", "route_log": log}

    node.__name__ = name
    return node


def _route(state: CrewState) -> str:
    return "end" if state.get("error") else "next"


def _role_models(
    model: Any | None, models: dict[str, Any] | None
) -> dict[str, Any]:
    if models is not None:
        return models
    if model is None:
        raise ValueError("build_graph requires model or models")
    return {name: model for name, _ in NODE_FIELDS}


def build_graph(
    model: Any | None = None,
    models: dict[str, Any] | None = None,
    bindings: dict[str, Binding] | None = None,
):
    role_models = _role_models(model, models)
    graph = StateGraph(CrewState)
    for name, field in NODE_FIELDS:
        binding = bindings.get(name) if bindings else None
        graph.add_node(name, _make_node(name, field, role_models[name], binding))
    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _route, {"next": "research", "end": END})
    graph.add_conditional_edges("research", _route, {"next": "eval", "end": END})
    graph.add_conditional_edges("eval", _route, {"next": "delivery", "end": END})
    graph.add_edge("delivery", END)
    return graph.compile()


def run_crew(
    customer_ask: str,
    model: Any | None = None,
    models: dict[str, Any] | None = None,
    bindings: dict[str, Binding] | None = None,
) -> CrewState:
    graph = build_graph(model=model, models=models, bindings=bindings)
    return graph.invoke(_empty_state(customer_ask))


def _load_dotenv() -> None:
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _live_models(bindings: dict[str, Binding]) -> dict[str, Any]:
    from langchain_openai import ChatOpenAI

    cache: dict[str, Any] = {}
    models: dict[str, Any] = {}
    base = os.environ.get("OPENAI_BASE_URL")
    for role, binding in bindings.items():
        if binding.model not in cache:
            kwargs: dict[str, Any] = {"model": binding.model}
            if base:
                kwargs["base_url"] = base
            cache[binding.model] = ChatOpenAI(**kwargs)
        models[role] = cache[binding.model]
    return models


def main() -> None:
    _load_dotenv()
    require_api_key()
    ask = " ".join(sys.argv[1:]).strip()
    if not ask:
        print(
            'Usage: uv run fde-crew "customer wants X under constraint Y"',
            file=sys.stderr,
        )
        raise SystemExit(1)
    bindings = resolve_all(ask)
    state = run_crew(ask, models=_live_models(bindings), bindings=bindings)
    for entry in state.get("route_log", []):
        print(
            f"[{entry['role']}] {entry['model']}  {entry['tier']}  {entry['reason']}"
        )
    print()
    sections = (
        ("intake", "engagement_brief"),
        ("research", "research_brief"),
        ("eval", "eval_plan"),
        ("delivery", "delivery"),
    )
    for label, field in sections:
        print(f"## {label}\n")
        print(state.get(field) or "")
        print()
    if state.get("error"):
        print(f"error: {state['error']}", file=sys.stderr)
        raise SystemExit(1)
