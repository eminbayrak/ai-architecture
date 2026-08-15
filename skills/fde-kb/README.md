# FDE knowledge base skill

Internal Poolside Agent Skill. Obsidian vault is the source of truth. A local SQLite file is search memory: FTS5 (BM25) always, plus sqlite-vec fused with RRF when a local Model2Vec snapshot (`minishlab/potion-base-8M`, 256-d) is present. Without that snapshot it runs lexical-only and says so. Notes never leave the machine and no model is ever called over the network.

System design (retrieval, write path, launcher gates, trust boundary): [docs/architecture.md](docs/architecture.md).

This is the same architecture used in production Obsidian retrievers: chunk on headings, dual index in one SQLite file, Reciprocal Rank Fusion. The skill does not ship a sample vault. Staff fill the company vault. Eval uses a golden JSONL that lives in that vault.

**Copy-over:** this entire `fde-kb/` folder is self-contained. Place it at `.poolside/skills/fde-kb`. You do not need the rest of a parent kit.

## Setup

Needs Python 3.12+ and `uv`. On Windows, `fde-kb.cmd` uses PowerShell (built-in) to launch `uv`, same pattern as the Jira skill. Tests set `FDE_KB_EMBEDDER=fake` and never hit the network.

The supported install path has no route to public PyPI or Hugging Face:

- Point `uv` at the internal index. Set `FDE_KB_UV_INDEX` (or `UV_DEFAULT_INDEX`) in the environment or repo-root `.env`. All three launchers export that as `UV_DEFAULT_INDEX` / `UV_INDEX_URL` before `uv run --script`. If it is unset, the launcher prints one line and exits 1. It does not fall back to public PyPI.
- Clone and index. Search works immediately with lexical FTS5. Model weights are **not** in git. Hybrid / semantic embeddings load only if the approved `potion-base-8M` snapshot is already on the machine (`FDE_KB_MODEL`, or `~/.cache/fde-kb/models/potion-base-8M`). If it is absent, `index` / `search` print one line and continue lexical-only. Hugging Face is not contacted.

Development only (laptops/CI): `FDE_KB_ALLOW_PUBLIC_INDEX=1`. Leave it unset when packages must come from your configured index.

Optional: Obsidian **1.12.4+** installer. Settings → General → enable **Command line interface**. Index and search work from disk without it.

Env (repo-root `.env` is fine; the launcher walks up from cwd and from the script):

```
FDE_KB_VAULT=C:\vaults\your-vault
FDE_KB_VAULT_NAME=your-vault
```

macOS / Linux:

```
FDE_KB_VAULT=/absolute/path/to/your-vault
FDE_KB_VAULT_NAME=your-vault-name
```

Optional: `FDE_KB_DB`. Defaults:

- Windows: `%LOCALAPPDATA%\fde-kb\index.sqlite`
- macOS / Linux: `~/.cache/fde-kb/index.sqlite`
- Tests (`FDE_KB_EMBEDDER=fake`): `index-hash-256.sqlite` in the same directory, so a fake embedder cannot overwrite a live index

Do not commit the DB. Do not commit model weight files. `FDE_KB_OFFLINE=1` still sets `HF_HUB_OFFLINE`; it is not required for lexical search.

Index, then search:

```bat
.poolside\skills\fde-kb\scripts\fde-kb.cmd index
.poolside\skills\fde-kb\scripts\fde-kb.cmd search "your query"
```

```bash
./skills/fde-kb/scripts/fde-kb index
./skills/fde-kb/scripts/fde-kb search "your query"
```

Override the interpreter with `FDE_KB_PYTHON` if needed. Optional `FDE_KB_OBSIDIAN` if the desktop app is not on PATH (`Obsidian.exe` on Windows, `obsidian` binary on macOS).

On Windows, sqlite-vec needs a Python that can load SQLite extensions. The PowerShell launcher probes `py -3` / `python` for that, then `uv run --python` that interpreter. On macOS the bash launcher prefers Homebrew Python for the same reason. If vec0 cannot load, search still runs FTS5 plus in-process cosine over stored embeddings, and `status.warnings` says so.

On Windows, `Obsidian.exe` is a GUI-subsystem binary and often returns empty CLI stdout. When `status.obsidian_cli` is false, `get` / `ingest` / `append` use the vault files on disk.

`search` JSON includes `indexed_at` and `stale`. `eval` reports recall@k and MRR against `<vault>/evals/golden.jsonl` (or `--golden` / `FDE_KB_GOLDEN`). A genuine embedder change requires `index --force`.

## Vault layout

Notes must match `assets/schemas/note.schema.json` (`title`, `type`, `tags`):

```
playbooks/      how we run work
engagements/    per-engagement notes
evals/          eval notes, plus golden.jsonl for retrieval eval
```

Golden JSONL schema: `assets/schemas/golden-case.schema.json`. One object per line: `{"query": "...", "path": "playbooks/....md"}`.

This repo ships templates and tests. Pytest builds temp notes in `tmp_path`. It does not ship company notes.

## Agent discovery

Agent Skills format (`SKILL.md`). Poolside scans `.poolside/skills/`, not `skills/`. Git symlinks do not work on a normal Windows clone. After checkout:

```bat
py -3 scripts\link-skills.py
```

```bash
python scripts/link-skills.py
```

That creates directory junctions on Windows (no Developer Mode, no elevation) or relative symlinks on macOS / Linux. If an existing clone already has a **file** named `.poolside/skills/fde-kb` whose contents are `../../skills/fde-kb`, delete that file and rerun the script.

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

Writes prefer the Obsidian desktop CLI. If the CLI is off, `ingest` / `append` / `get` still use the vault files on disk.

## Getting content in

Nobody writes frontmatter by hand. The agent picks `--type`, `--title`, and `--tags` from what the user said, and this skill renders the schema-valid note. `--tags` always gets the type prepended, so `--tags "latency,serving"` on a playbook yields `[playbook, latency, serving]`.

`--body` travels on the command line, and Windows caps that at 8191 characters. Anything longer than a couple of paragraphs must use `--body-file PATH`, or `--body-file -` to read stdin.

`import PATH` adopts a file that already exists. Title precedence is `--title`, then the file's frontmatter `title`, then its first H1, then the filename. Existing tags are inherited and a duplicated leading H1 is dropped.

## Test vault

`python scripts/make-test-vault.py --dest /tmp/kb-test-vault` (from inside this skill folder) writes a throwaway vault of schema-valid notes plus a `golden.jsonl`, for verifying an install. It refuses to write into a directory that already holds markdown. It is a plumbing check, not a quality measurement: six notes against the default `k=8` cannot fail, so run `eval -k 1` if you want a number that can move.

More docs in this folder: [architecture](docs/architecture.md).
