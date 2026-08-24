# FDE knowledge base skill

Internal Poolside Agent Skill. Obsidian vault is the source of truth. A local SQLite file is search memory: FTS5 (BM25) always, plus sqlite-vec fused with RRF when a local Model2Vec snapshot (`minishlab/potion-base-8M`, 256-d) is present. Without that snapshot it runs lexical-only and says so. Notes never leave the machine and no model is ever called over the network.

System design: [docs/architecture.md](docs/architecture.md). Demo script: [docs/demo-walkthrough.md](docs/demo-walkthrough.md).

This is the same pattern used in many local Obsidian retrievers: chunk on headings, dual index in one SQLite file, Reciprocal Rank Fusion when vectors are available. The skill does not ship a sample vault of real notes. Eval uses a golden JSONL that lives in that vault.

**Copy-over:** this entire `fde-kb/` folder is self-contained. Place it at `.poolside/skills/fde-kb`. You do not need the rest of a parent kit.

Sibling skills (optional, separate installs): `graph-memory` for typed hops, `llm-wiki` for a compiled page wiki. Shipped demo: open `docs/retrieval-benchmark.html` at the repo root. Against another repo: `py -3 scripts/retrieval-bench.py --repo PATH`.

## Quick setup

Needs Python 3.12+ and `uv` on PATH. On Windows, `fde-kb.cmd` uses PowerShell to launch `uv`.

### 1. Put the skill where Poolside can see it

Copy this folder to:

```text
.poolside/skills/fde-kb/
```

### 2. Create a project-root `.env`

Put a file named `.env` next to `.poolside` (project root), not inside the skill folder. The launcher walks up from the current directory and from the script, so it will find it.

**Demo (no internal package index yet):**

```env
FDE_KB_VAULT=C:\Temp\kb-test-vault
FDE_KB_DB=C:\Temp\kb-test.sqlite
FDE_KB_ALLOW_PUBLIC_INDEX=1
```

**Normal (packages from your org's Python index):**

```env
FDE_KB_VAULT=C:\vaults\your-vault
FDE_KB_DB=C:\vaults\your-vault\.fde-kb\index.sqlite
FDE_KB_UV_INDEX=https://your-org-pypi.example/simple
```

| Variable | What it is |
|----------|------------|
| `FDE_KB_VAULT` | Folder of markdown notes (Obsidian vault or test vault). Absolute path. |
| `FDE_KB_DB` | Where to write the **search** SQLite file (derived from the vault). Optional; default `%LOCALAPPDATA%\fde-kb\index.sqlite` on Windows. |
| `FDE_KB_VAULT_NAME` | Optional Obsidian vault name for the desktop CLI. Defaults to the folder name. |
| `FDE_KB_UV_INDEX` | URL of a **Python package** index for `uv` (see below). Not a folder path. Not the SQLite DB. |
| `FDE_KB_ALLOW_PUBLIC_INDEX` | `1` = allow public PyPI for wheels (demo / CI only). |
| `FDE_KB_MODEL` | Optional path to a local Model2Vec snapshot folder. Hybrid search only. |

Same values in PowerShell for one session (no `.env`):

```powershell
$env:FDE_KB_VAULT="C:\Temp\kb-test-vault"
$env:FDE_KB_DB="C:\Temp\kb-test.sqlite"
$env:FDE_KB_ALLOW_PUBLIC_INDEX="1"
```

### 3. Optional: generate a throwaway vault

```powershell
python .\.poolside\skills\fde-kb\scripts\make-test-vault.py --dest C:\Temp\kb-test-vault
```

Point `FDE_KB_VAULT` at that path. Do not merge those notes into a real vault.

### 4. Smoke test

```powershell
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd status
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd index
.\.poolside\skills\fde-kb\scripts\fde-kb.cmd search "your query"
```

Then ask Poolside: search the KB for latency budgets.

## Python packages the skill needs (`sqlite-vec`, `model2vec`)

On first run, `uv` downloads ordinary Python packages (`.whl` files), the same kind
`pip install` uses. Examples of what those files look like:

```text
sqlite_vec-0.1.6-...-win_amd64.whl
model2vec-0.4.1-py3-none-any.whl
```

Plus smaller dependency wheels (`numpy`, etc.). That is **not** the Model2Vec
weight file `model.safetensors`. Weights are optional and separate
([assets/models/README.md](assets/models/README.md)).

### How to set this in `.env` (pick one)

**Option A - demo / laptop with internet (what you use now)**

Leave `FDE_KB_UV_INDEX` out. Tell the launcher it may use public PyPI:

```env
FDE_KB_VAULT=C:\Temp\kb-test-vault
FDE_KB_DB=C:\Temp\kb-test.sqlite
FDE_KB_ALLOW_PUBLIC_INDEX=1
```

**Option B - company already hosts those `.whl` files on an internal website**

Someone gives you a package URL that ends in `/simple` (or `/simple/`). Put
**only that URL** in `.env`. Do **not** set `FDE_KB_ALLOW_PUBLIC_INDEX`.

```env
FDE_KB_VAULT=C:\Temp\kb-test-vault
FDE_KB_DB=C:\Temp\kb-test.sqlite
FDE_KB_UV_INDEX=https://packages.mycompany.example/pypi/simple
```

Real URLs look like one of these (examples only; use the one your company uses):

```env
FDE_KB_UV_INDEX=https://artifacts.mycompany.com/artifactory/api/pypi/pypi-remote/simple
FDE_KB_UV_INDEX=https://pkgs.dev.azure.com/MyOrg/MyProject/_packaging/MyFeed/pypi/simple/
FDE_KB_UV_INDEX=https://nexus.mycompany.com/repository/pypi-group/simple
```

If that URL works, opening something like
`https://.../simple/sqlite-vec/` in a browser (when logged in) lists wheel
filenames. You do not paste the `.whl` path into `.env`. You paste the **index
root** URL (`.../simple`).

**Wrong (will not work):**

```env
# folder on disk - this is NOT a package index
FDE_KB_UV_INDEX=C:\Users\me\Documents\warlock\index
```

### Do I build that website myself?

No, not with this skill. Either:

1. Use Option A for a demo, or
2. Use Option B with a URL that already exists at work for Python installs.

If nobody has such a URL, stay on Option A.

## Setup details

Launchers export `FDE_KB_UV_INDEX` as `UV_DEFAULT_INDEX` / `UV_INDEX_URL` before `uv run --script`. If neither index URL nor `FDE_KB_ALLOW_PUBLIC_INDEX=1` is set, the launcher prints one line and exits 1.

Optional: Obsidian **1.12.4+** with Settings → General → **Command line interface**. Index and search work from disk without it.

Default `FDE_KB_DB` if unset:

- Windows: `%LOCALAPPDATA%\fde-kb\index.sqlite`
- macOS / Linux: `~/.cache/fde-kb/index.sqlite`
- Tests (`FDE_KB_EMBEDDER=fake`): `index-hash-256.sqlite` beside that default

Do not commit the DB or model weight files. `FDE_KB_OFFLINE=1` sets `HF_HUB_OFFLINE`; not required for lexical search.

```bat
.poolside\skills\fde-kb\scripts\fde-kb.cmd index
.poolside\skills\fde-kb\scripts\fde-kb.cmd search "your query"
```

```bash
./skills/fde-kb/scripts/fde-kb index
./skills/fde-kb/scripts/fde-kb search "your query"
```

Override interpreter with `FDE_KB_PYTHON` if needed. Optional `FDE_KB_OBSIDIAN` if the desktop app is not on PATH.

On Windows, sqlite-vec needs a Python that can load SQLite extensions. The PowerShell launcher probes `py -3` / `python`, then `uv run --python` that interpreter. If vec0 cannot load, search still runs FTS5, and `status.warnings` says so.

When `status.obsidian_cli` is false, `get` / `ingest` / `append` use vault files on disk.

`search` JSON includes `indexed_at` and `stale`. `eval` reports recall@k and MRR against `<vault>/evals/golden.jsonl`. After an embedder change, run `index --force`.

## Vault layout

Notes must match `assets/schemas/note.schema.json` (`title`, `type`, `tags`):

```
playbooks/      how we run work
engagements/    per-engagement notes
evals/          eval notes, plus golden.jsonl for retrieval eval
```

Golden JSONL schema: `assets/schemas/golden-case.schema.json`. One object per line: `{"query": "...", "path": "playbooks/....md"}`.

This skill ships templates and tests. Pytest builds temp notes in `tmp_path`. `scripts/make-test-vault.py` can generate a throwaway vault for demos.

## Agent discovery

Agent Skills format (`SKILL.md`). Poolside scans `.poolside/skills/`.

**Copy-over:** put this whole `fde-kb/` folder at `.poolside/skills/fde-kb`. No link script required.

**Kit layout** (`skills/` + `.poolside/skills/`): after clone, run `python scripts/link-skills.py` (Windows: directory junctions, no elevation). If `.poolside/skills/fde-kb` is a plain text file containing `../../skills/fde-kb`, delete it and copy or re-link.

Sandboxes do not mount the default index dir or a vault outside the workspace unless you add an extra mount.

## Commands

| Command | What |
|---------|------|
| `status` | vault, db, counts, embedder, sqlite-vec, obsidian CLI, schema_invalid, model_ready, uv_index |
| `index` | walk `.md`, skip `.obsidian/` and `.trash/`, upsert by sha256 |
| `search "q"` | hybrid RRF (default). `--mode lexical\|semantic\|hybrid --k 8 --tag --type --since --full` |
| `get PATH` | `obsidian vault=NAME read path=PATH`, else the vault file |
| `ingest --type playbook\|engagement\|eval --title T [--body B] [--body-file F] [--tags a,b]` | create via CLI or disk, then index |
| `import PATH --type T [--title T] [--tags a,b]` | adopt an existing document, deriving the title |
| `append PATH --body B` or `--body-file F` | append via CLI or disk, then reindex |
| `eval` | recall@k and MRR vs vault `evals/golden.jsonl` (or `--golden PATH`) |
| `index --force` | rebuild after an embedder or revision change |

Writes prefer the Obsidian desktop CLI. If the CLI is off, disk fallback is used.

## Getting content in

Nobody writes frontmatter by hand. The agent picks `--type`, `--title`, and `--tags`. `--tags` always gets the type prepended.

`--body` is limited by the OS command line (8191 characters on Windows `cmd`). Longer text: `--body-file PATH` or `--body-file -` (stdin).

`import PATH` adopts an existing file. Title: `--title`, else frontmatter, else H1, else filename.

## Test vault

```powershell
python .\.poolside\skills\fde-kb\scripts\make-test-vault.py --dest C:\Temp\kb-test-vault
```

Plumbing check only. For eval that can move, use `eval -k 1`.
