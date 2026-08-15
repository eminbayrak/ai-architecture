# fde-lab — design spec

Date: 2026-08-13
Status: approved
Repo: public `github.com/eminbayrak/fde-lab` (create + push only after explicit confirmation)

## Problem

Emin is an FDE and SWE. Skills, agent prompts, and LangGraph graphs currently live in personal home paths (`~/.claude/skills`, ad-hoc project folders). There is no public, portable kit that shows how an FDE runs a customer engagement with agents: intake, research, eval-before-code, delivery.

This repo is that kit. Day 1 is a runnable starter, not a platform.

## Goals

1. Public GitHub repo that reads as an FDE/SWE portfolio artifact (same class as `retrace`).
2. `skills/` contains a portable copy of `transcribe-video` plus the `watch` pipeline it depends on.
3. `agents/` contains portable markdown roles plus one runnable LangGraph graph: the FDE crew.
4. No secrets, no machine-specific paths, no iCloud/KB vault hardcoding.
5. Local `uv run` is enough. No GitHub push until Emin confirms.

## Non-goals (day 1)

- Auth, database, API server, queue, LangSmith-required tracing
- Wiring `transcribe-video` as a LangGraph tool
- Human-in-the-loop interrupts
- Copying `youtube-transcript-mcp`, `watch-local`, or `agentic-workflow`
- Overnight orchestrators, worktree managers, or a skills marketplace

## Repository layout

```
fde-lab/
  README.md
  AGENTS.md
  LICENSE                 MIT (repo); watch skill keeps upstream MIT attribution
  pyproject.toml
  .env.example
  .gitignore
  docs/superpowers/specs/2026-08-13-fde-lab-design.md
  skills/
    transcribe-video/SKILL.md
    watch/SKILL.md
    watch/scripts/        copied from ~/.claude/skills/watch/scripts
  agents/
    README.md
    prompts/
      intake.md
      research.md
      eval.md
      delivery.md
    graphs/
      fde_crew.py
      __init__.py
    __init__.py
  tests/
    test_fde_crew.py
```

Python package root is the repo (`agents` is importable). Entry point: `fde-crew`.

## Stack

- Python 3.12+
- uv for env and lockfile
- langgraph, langchain-openai (OpenAI-compatible; Groq via `OPENAI_BASE_URL` if desired)
- pytest
- No poetry, no conda

LLM is read from env: `OPENAI_API_KEY` required for a live run. Tests never call a live model.

## Agents: FDE crew graph

Mirrors a customer engagement, not a generic chatbot swarm.

### State

```python
class CrewState(TypedDict):
    customer_ask: str
    engagement_brief: str
    research_brief: str
    eval_plan: str
    delivery: str
    error: str
```

`error` is empty on success. A node that fails writes a one-line reason and the graph stops. There is no `artifacts` list on day 1 (no tools to fetch files).

### Nodes (sequential)

| Node | Prompt file | Writes | Job |
|------|-------------|--------|-----|
| intake | `agents/prompts/intake.md` | `engagement_brief` (problem, constraints, success criteria) | Turn a messy ask into an engagement brief |
| research | `agents/prompts/research.md` | `research_brief` | What to inspect, named risks, missing artifacts |
| eval | `agents/prompts/eval.md` | `eval_plan` | Golden cases, failure modes, definition of done before code |
| delivery | `agents/prompts/delivery.md` | `delivery` | Implementation plan + customer-facing writeup in FDE voice |

No tools on day 1 (no web search, no repo crawler, no transcript tool). Each node is one LLM call with the prompt plus current state.

### CLI

```
uv run fde-crew "customer wants X under constraint Y"
```

Prints each node's output as it finishes. Exit 1 if `error` is set or `OPENAI_API_KEY` is missing (message points at `.env.example`).

### Prompts

Portable markdown. No Cursor/Claude/LangGraph syntax in the body. Role, rules, output shape, stop conditions. FDE voice: lead with the customer outcome, name risks, prefer eval-before-code, do not over-promise.

## Skills: transcribe-video + watch

Copy, do not symlink.

Source of truth on the machine:

- `~/.claude/skills/transcribe-video/SKILL.md` (operator-facing wrapper)
- `~/.claude/skills/watch/` (engine: SKILL.md + scripts)

### Sanitization (required for public)

1. Replace home-path watch script invocations with a sibling path: `<transcribe-video-dir>/../watch/scripts/...`. In the skill text, prefer `${CLAUDE_SKILL_DIR}` when set (Claude Code); otherwise the directory that contains that skill's `SKILL.md`. Never hardcode a home path.
2. Replace iCloud KB vault paths with `$WATCH_VAULT_DIR`. If unset, skip vault ingest and say so. Keep the KB summary template.
3. Do not copy `~/.config/watch/.env`, API keys, or any vault notes.
4. Keep watch's MIT license and upstream attribution (`taoufik123-collab/claude-watch`).
5. `transcribe-video` remains the operator-facing skill (captions vs full pipeline, then the KB-summary offer). `watch` remains the engine.

Skills are for Cursor/Claude harnesses. They are not imported by the LangGraph graph on day 1.

## Error handling

- Missing `OPENAI_API_KEY`: fail before any LLM call; print how to copy `.env.example`.
- Node exception: catch, set `state["error"]`, halt remaining nodes, exit 1.
- Watch/transcribe skills: keep upstream preflight (`setup.py --check`). Missing ffmpeg/yt-dlp/key is the skill's problem, not the graph's.

## Testing

- `tests/test_fde_crew.py` uses a stub/fake chat model (no network).
- Assert the graph compiles.
- Assert a fixture `customer_ask` produces non-empty `engagement_brief`, `research_brief`, `eval_plan`, `delivery`.
- Assert a stubbed node failure sets `error` and skips later nodes (or stops the graph).
- CI: `uv run pytest`. No live LLM.

## README positioning

Title the repo as an FDE/SWE agent kit, not a tutorial dump.

Cover, in this order:

1. What it is (one paragraph)
2. FDE crew: how to run
3. Skills: how to drop `skills/transcribe-video` and `skills/watch` into `~/.claude/skills` or `.cursor/skills`
4. Layout
5. Attribution for watch

`AGENTS.md` stays short: writing (no em-dashes), quality bar, prefer CLI over MCP, run tests before claiming done.

## Push gate

Local git init and commits are fine during implementation. `gh repo create` and `git push` wait for an explicit yes in chat. Show the tree, README, and `git status` before asking.

## Success criteria

- `uv run fde-crew "Acme wants a RAG eval harness in two weeks with PHI constraints"` prints four sections locally (when a key is set).
- `uv run pytest` passes with no network.
- Someone else can clone the public repo, install the two skills, and run them without editing Emin-specific paths.
- No absolute home paths (`/Users/...`) remain in tracked files.
