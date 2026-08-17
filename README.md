# ai-architecture

FDE + AI Architect kit for multi-agent work. A LangGraph crew turns a customer ask into intake, research, eval-before-code, and delivery. An architect-defined routing policy sends cheap models at structured work and stronger models at architecture, evals, and customer writing. `skills/` ships a portable video-transcript pipeline, a Jira Data Center skill (Bearer PAT, OS fallbacks), and an FDE knowledge base skill (Obsidian vault + local sqlite-vec RAG).

This is a starter kit, not a platform. No auth, no database, no required tracing. Routing does not call an LLM.

**Architecture (fde-kb):** [skills/fde-kb/docs/architecture.md](skills/fde-kb/docs/architecture.md) - retrieval, write path, launcher gates, trust boundary. Mermaid nodes are clickable. The skill folder is self-contained for copy into `.poolside/skills/`.

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

## Skills

Copy or symlink the folders you need. Skills use the Agent Skills format (`SKILL.md`). Poolside scans `.poolside/skills/`, not `skills/`. After a clone, create the harness links (Windows directory junctions, no elevation):

```bat
py -3 scripts\link-skills.py
```

```bash
python scripts/link-skills.py
```

**Jira (company Data Center):** `skills/jira` — Bearer PAT. One launcher: bash, else PowerShell, else Python stdlib. No extra installs. Token: `~/.config/atlassian/jira.token` or `%USERPROFILE%\.config\atlassian\jira.token`. See `skills/jira/README.md`.

**FDE knowledge base:** `skills/fde-kb` - Obsidian vault as source of truth, local SQLite as search memory (FTS5 always; sqlite-vec + Model2Vec when a local snapshot is present). Copy the folder to `.poolside/skills/fde-kb`, set `FDE_KB_VAULT`, then `index` / `search`. See `skills/fde-kb/README.md` and `skills/fde-kb/docs/demo-walkthrough.md`.

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
  skills/transcribe-video/
  skills/watch/
  tests/
```

## Attribution

The `watch` engine is MIT-licensed work from [taoufik123-collab/claude-watch](https://github.com/taoufik123-collab/claude-watch). See `skills/watch/NOTICE`.
