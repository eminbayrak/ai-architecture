# fde-kb demo walkthrough

Use this script to show the skill end to end. Goal: prove Poolside can search and
write a local Obsidian-style vault without sending note text to a cloud embedder.

Audience: engineers who know Obsidian / Poolside, not RAG specialists.

Time: about 10 to 15 minutes.

---

## One-sentence pitch

The vault is the source of truth. A local SQLite file is search memory. Poolside
runs one command; the agent answers from the hits.

---

## What you need before the room

| Item | Demo choice |
|------|-------------|
| Skill folder | `.poolside/skills/fde-kb/` (full copy of this skill) |
| Scratch vault | Generated with `make-test-vault.py` (do not use a real shared vault) |
| Scratch DB | Separate sqlite path so you do not pollute a real index |
| Packages | Internal `FDE_KB_UV_INDEX` **or**, demo-only, `FDE_KB_ALLOW_PUBLIC_INDEX=1` |
| Model2Vec | Optional. Without it, search is BM25 keyword only (still a full demo) |

Example `.env` at the project root (demo-only public packages):

```
FDE_KB_VAULT=C:\Temp\kb-test-vault
FDE_KB_DB=C:\Temp\kb-test.sqlite
FDE_KB_ALLOW_PUBLIC_INDEX=1
```

On a managed laptop, prefer an internal index URL and drop `FDE_KB_ALLOW_PUBLIC_INDEX`
as soon as you have one. See [Getting Model2Vec on a company laptop](#getting-model2vec-on-a-company-laptop).

Generate the vault once:

```powershell
python .\.poolside\skills\fde-kb\scripts\make-test-vault.py --dest C:\Temp\kb-test-vault
```

---

## Architecture in 60 seconds (say this)

```text
Human ask
   -> Poolside reads SKILL.md
   -> runs fde-kb.cmd (status | index | search | ingest | ...)
   -> fde_kb.py
         reads/writes vault markdown
         reads/writes local index.sqlite
   -> JSON back to Poolside
   -> Poolside answers and cites note paths
```

Two search modes:

| Mode | When | How |
|------|------|-----|
| Lexical | Always; default if no model | SQLite FTS5 + **BM25** keyword ranking |
| Hybrid | Local Model2Vec snapshot present | BM25 + vector search, fused with RRF |

No Hugging Face call at query time. No cloud embedding API for note text.

---

## Demo flow (follow in order)

### 0. Open the room

Say:

> We are not replacing Obsidian. We are showing a Poolside skill that indexes the
> Playbook folder and lets the agent search it locally.

Show the folder tree briefly: `playbooks/`, `engagements/`, `evals/`.

### 1. Prove the CLI (terminal)

From the repo root in PowerShell:

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd status
```

**Call out:**

- `vault` and `db` match `.env`
- `sqlite_vec: true`
- `model_ready: false` is OK for this demo
- Warning says Hugging Face is not contacted

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd index
```

**Call out:** notes/chunks updated, `errors: 0`.

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd search "when should we pin down response time targets"
```

**Call out:** top hit is `playbooks/latency-budgets.md`, `"mode": "lexical"`, `"source": "fts"`.

### 2. Prove the agent (Poolside chat)

Ask in plain English (or invoke the skill via `/` / `$` if your Poolside UI has that):

> Search the KB for latency budgets

**Call out:**

- Poolside runs `.poolside\skills\fde-kb\scripts\fde-kb.cmd search "..."`
- It may say degraded / lexical-only (correct without the model)
- Answer cites `playbooks/latency-budgets.md`

Optional second ask:

> What do we know about writing evaluations before building?

Expect something under `playbooks/eval-before-code.md`.

### 3. Prove write path (optional, 2 minutes)

Short note via the agent or CLI:

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd ingest --type playbook --title "Demo note from walkthrough" --tags "demo" --body "This note was created during the fde-kb demo. It should become searchable after ingest."
```

Then:

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd search "demo walkthrough"
```

**Call out:** humans do not write YAML frontmatter; the skill renders schema-valid notes.

Longer docs: `--body-file PATH` (Windows command line is capped at 8191 characters).

Existing file on disk:

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd import C:\Temp\some-doc.md --type playbook
```

### 4. Prove filters (optional)

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd search "budget" --type playbook --tag latency
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd get playbooks/latency-budgets.md
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd eval -k 1
```

`eval -k 1` is a plumbing check against `evals/golden.jsonl` in the test vault.
It is not a real quality score for production.

### 5. Close the demo

Say:

> Today we proved: copy skill folder, index a vault, Poolside searches it, answers
> cite real paths, notes stay on the machine. Next decisions are content supply,
> where the shared vault lives, package install via internal index, and whether
> we place an approved Model2Vec snapshot for hybrid search.

---

## Feature map (if someone asks “what else?”)

| Feature | Command | What to say |
|---------|---------|-------------|
| Health | `status` | Paths, counts, model_ready, warnings |
| Build / refresh index | `index` (`--force` after embedder change) | Derived sqlite; vault is source of truth |
| Search | `search "q"` | BM25 always; hybrid if model present |
| Read note | `get PATH` | From Obsidian CLI if available, else disk |
| Create note | `ingest --type --title [--body\|--body-file] [--tags]` | Agent fills schema |
| Adopt file | `import PATH --type` | Title from frontmatter / H1 / filename |
| Append | `append PATH --body` | Then reindex that note |
| Retrieval check | `eval [-k 1]` | Golden JSONL in the vault |

Modes: `--mode hybrid|lexical|semantic` (default hybrid; reports what actually ran).

---

## FAQ you will get in the room

**Is this GraphRAG / LangGraph?**  
No. This skill is a leaf CLI. Zero LLM calls inside the tool. Poolside (or a separate crew) is the orchestrator.

**Why degraded?**  
No local Model2Vec weights. Lexical BM25 still works. That is intentional.

**Did we hit Hugging Face?**  
No. Status/search say so when the snapshot is missing.

**Did we hit public PyPI?**  
Only if `FDE_KB_ALLOW_PUBLIC_INDEX=1`. For a real rollout, point `FDE_KB_UV_INDEX` at the internal package index and remove that flag.

**Obsidian CLI missing?**  
Fine. Writes and reads use vault files on disk. Enabling Obsidian’s CLI is optional.

**Where do real notes come from?**  
Not this PoC. Harvest from existing docs, tickets, transcripts; or ask the agent to `ingest` after a meeting. Empty vault = empty product.

---

## Getting Model2Vec on a company laptop

You do **not** download from Hugging Face on a managed machine. The skill never
fetches weights at runtime when public access is off.

### What the skill expects

Approved snapshot of `minishlab/potion-base-8M` at revision
`bf8b056651a2c21b8d2565580b8569da283cab23`, containing at least:

- `config.json`
- `model.safetensors`

Place it at one of:

```
%LOCALAPPDATA%\fde-kb\models\potion-base-8M\
```

or set:

```
FDE_KB_MODEL=C:\path\to\that\folder
```

Then:

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd index --force
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd status
```

Expect `model_ready: true`. Search can run hybrid.

### How to get the files onto the laptop (process, not a URL)

1. **Treat the snapshot as a third-party artifact**, same as any approved binary.
2. Ask whoever owns **software approval / internal artifact store** to host that
   exact revision (hash pinned above).
3. Ops or you copy it from the **internal** share or artifact URL onto the path
   above (USB / SCCM / approved download portal: whatever your org already uses
   for approved tools).
4. Confirm with `status` that `model_ready` is true and that logs still say
   Hugging Face is not contacted.

Packages (`sqlite-vec`, `model2vec` wheels) follow the same rule: set
`FDE_KB_UV_INDEX` to the **internal** Python index. Do not rely on
`FDE_KB_ALLOW_PUBLIC_INDEX` outside a short demo.

### If approval is slow

Ship lexical-only. The PoC and day-to-day keyword search do not block on the model.

---

## Cleanup after the demo

- Delete `C:\Temp\kb-test-vault` and `C:\Temp\kb-test.sqlite` if they were scratch.
- Remove `FDE_KB_ALLOW_PUBLIC_INDEX` from `.env` on the company machine when done.
- Do not merge test notes into a real Playbook vault.
