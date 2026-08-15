from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agents.graphs.fde_crew import build_graph, require_api_key, run_crew


def _model(texts: list[str] | None = None) -> FakeListChatModel:
    return FakeListChatModel(
        responses=texts
        or [
            "ENGAGEMENT BRIEF",
            "RESEARCH BRIEF",
            "EVAL PLAN",
            "DELIVERY",
        ]
    )


class BoomModel:
    def invoke(self, messages):
        raise RuntimeError("llm down")


def test_graph_compiles():
    graph = build_graph(_model())
    assert graph is not None


def test_fixture_ask_fills_all_fields():
    state = run_crew(
        "Acme wants a RAG eval harness in two weeks with PHI constraints",
        _model(),
    )
    assert state["engagement_brief"] == "ENGAGEMENT BRIEF"
    assert state["research_brief"] == "RESEARCH BRIEF"
    assert state["eval_plan"] == "EVAL PLAN"
    assert state["delivery"] == "DELIVERY"
    assert state["error"] == ""


def test_node_failure_sets_error_and_skips_later_nodes():
    state = run_crew("x", BoomModel())
    assert "llm down" in state["error"]
    assert state["research_brief"] == ""
    assert state["eval_plan"] == ""
    assert state["delivery"] == ""


def test_route_log_has_four_entries():
    state = run_crew("Acme wants a RAG eval harness", _model())
    assert len(state["route_log"]) == 4
    assert [e["role"] for e in state["route_log"]] == [
        "intake",
        "research",
        "eval",
        "delivery",
    ]


def test_missing_api_key_exits(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        require_api_key()
        raise AssertionError("should have exited")
    except SystemExit as exc:
        assert exc.code == 1
