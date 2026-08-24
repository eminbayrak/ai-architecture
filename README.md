# ai-architecture

FDE + AI Architect kit for multi-agent work. A LangGraph crew turns a customer ask into intake, research, eval-before-code, and delivery. An architect-defined routing policy sends cheap models at structured work and stronger models at architecture, evals, and customer writing. `skills/` ships harness skills (Agent Skills format for Poolside / Codex / Claude Code), a portable video-transcript pipeline, and a retrieval benchmark that scores three retrieval paths against any repo or vault.

This is a starter kit, not a platform. No auth, no database, no required tracing. Routing does not call an LLM.

**Architecture (fde-kb):** [skills/fde-kb/docs/architecture.md](skills/fde-kb/docs/architecture.md) - retrieval, write path, launcher gates, trust boundary. Mermaid nodes are clickable. The skill folder is self-contained for copy into `.poolside/skills/`.

**Three retrieval skills, install separately:** `fde-kb` (pull search), `graph-memory` (typed triples), `llm-wiki` (compiled wiki). They do not import each other. Shipped demo: [docs/retrieval-benchmark.html](docs/retrieval-benchmark.html).

## Retrieval bench (GitHub repo, local folder, vault, or multi-hop demo)

One command writes a report in the **OS temp folder** and opens it in your browser. **No model API key** for this bench. Skills are for Poolside / Codex / Claude Code: the harness agent does llm-wiki ingest when the skill says so. The bench only scores retrieve (or script compile-extracts / `--wiki`).

Install the four retrieval skills into Poolside (once after clone):

```bat
py -3 scripts\link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
```

```bash
python3 scripts/link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
```

**Multi-hop demo (refund / who signs off — graph-memory’s strength):**

```bat
py -3 scripts\retrieval-bench.py --demo multihop
```

```bash
uv run python3 scripts/retrieval-bench.py --demo multihop
```

**GitHub repo** (private: sign in with `gh auth login` or Git Credential Manager first):

```bat
py -3 scripts\retrieval-bench.py --repo https://github.com/org/private.git
```

```bash
uv run python3 scripts/retrieval-bench.py --repo https://github.com/org/repo
```

**Obsidian / fde-kb vault** (`playbooks/`, `engagements/`, `evals/`, or `.obsidian/`):

```bat
py -3 scripts\retrieval-bench.py --repo C:\vaults\FDE-vault
```

```bash
python3 scripts/retrieval-bench.py --repo ~/vaults/FDE-vault
```

Default output: `%TEMP%\retrieval-bench\run-*` (Windows) or `$TMPDIR/retrieval-bench/run-*` (macOS). Pass `--no-open` to skip the browser.

See [skills/retrieval-bench/README.md](skills/retrieval-bench/README.md) for all modes and flags.

## FDE crew

```bash
uv sync --group dev
cp .env.example .env   # set OPENAI_API_KEY
uv run fde-crew "Acme wants a RAG eval harness in two weeks with PHI constraints"
uv run pytest
```

The CLI prints `[role] model  tier  reason` before each section so you can see the policy fire.

### Default routing

| Role | Specialty | economy | standard / high |
|------|-----------|---------|-----------------|
| intake | structured extraction | gpt-4.1-mini | gpt-4.1-mini |
| research | scoping / risks | gpt-4.1-mini | gpt-4.1-mini |
| eval | architecture + evals | gpt-4.1-mini | gpt-4.1 |
| delivery | plan + customer writing | gpt-4.1-mini | gpt-4.1 |

Policy lives in `agents/routing/catalog.yaml`. Heuristics bump the tier to `high` when the ask mentions PHI/HIPAA/PII, multi-system/greenfield, or hard SLOs. `economy` is opt-in so a PHI ask cannot silently go cheap.

```bash
FDE_TIER_FORCE=economy uv run fde-crew "cheap full run"
FDE_MODEL_EVAL=gpt-4.1-mini uv run fde-crew "override eval only"
```

Optional: `OPENAI_BASE_URL` for an OpenAI-compatible endpoint.

The graph is sequential: **intake → research → eval → delivery**. Prompts live in `agents/prompts/`. Day 1 has no tools (no web search, no repo crawler).

## Skills and Poolside

Copy or symlink the folders you need. Skills use the Agent Skills format (`SKILL.md`). **Poolside scans `.poolside/skills/` only**, not `skills/`. After a clone, create harness links (Windows directory junctions, no elevation):

```bat
py -3 scripts\link-skills.py
```

```bash
python3 scripts/link-skills.py
python3 scripts/link-skills.py --list
python3 scripts/link-skills.py --skills fde-kb
python3 scripts/link-skills.py --skills graph-memory,llm-wiki,retrieval-bench
```

You can also copy a skill folder into `.poolside/skills/<name>/` as a real directory (not a git-symlink text file). See [.poolside/skills/README.md](.poolside/skills/README.md).

**Jira (company Data Center):** `skills/jira` — Bearer PAT. One launcher: bash, else PowerShell, else Python stdlib. No extra installs. Token: `~/.config/atlassian/jira.token` or `%USERPROFILE%\.config\atlassian\jira.token`. See `skills/jira/README.md`.

**FDE knowledge base:** `skills/fde-kb` - Obsidian vault as source of truth, local SQLite as search memory (FTS5 always; sqlite-vec + Model2Vec when a local snapshot is present). Copy the folder to `.poolside/skills/fde-kb`, set `FDE_KB_VAULT`, then `index` / `search`. See `skills/fde-kb/README.md` and `skills/fde-kb/docs/demo-walkthrough.md`.

**Graph memory:** `skills/graph-memory` - closed ontology, three SQLite tables, recursive walk, CLI `recall`. No model call in the walker. Optional `compare` needs sibling fde-kb. See `skills/graph-memory/README.md`.

**llm-wiki:** `skills/llm-wiki` - raw notes stay immutable; the agent writes `wiki/`; CLI `query` / `lint` / `compile-extracts` make no model calls on retrieve. Copy this folder alone if that is the path you want. See `skills/llm-wiki/README.md` and `skills/llm-wiki/docs/benchmark.html`.

**Retrieval bench:** `skills/retrieval-bench` - one command against a repo or vault you have not read. Auto-generates questions, scores the sibling retrieval skills, writes `benchmark.html`. See `skills/retrieval-bench/README.md`.

**Transcripts:** `transcribe-video` is the operator-facing skill; `watch` is the engine it calls.

Set `WATCH_VAULT_DIR` if you want KB ingest after a transcript. If it is unset, the skill skips the vault write.

Needs `ffmpeg` and `yt-dlp` for the full pipeline. Whisper fallback keys go in `~/.config/watch/.env` (never commit them).

## Layout

```
ai-architecture/
  agents/prompts/          portable FDE + AI Architect roles
  agents/routing/          catalog, heuristics, resolve()
  agents/graphs/fde_crew.py
  skills/jira/               Data Center PAT + curl wrapper
  skills/fde-kb/             Obsidian + sqlite-vec RAG (self-contained; copy this folder)
  skills/fde-kb/docs/        architecture for the skill
  skills/graph-memory/       typed graph, SQLite walk, model-free recall
  skills/llm-wiki/           compiled wiki, agent ingest, model-free query
  skills/retrieval-bench/    one-command HTML bench against any repo or vault
  docs/retrieval-benchmark.html  standalone accuracy / token / window report
  skills/transcribe-video/
  skills/watch/
  tests/
```

## Attribution

The `watch` engine is MIT-licensed work from [taoufik123-collab/claude-watch](https://github.com/taoufik123-collab/claude-watch). See `skills/watch/NOTICE`.
