# fde-kb architecture

How this skill works. Diagram nodes are clickable and open the implementing
file. If your viewer does not support clickable Mermaid, use the
[file index](#file-index).

This folder is the whole skill. Copy `fde-kb/` into `.poolside/skills/fde-kb`
(or your harness equivalent). Nothing here depends on a parent repo.

## Overview

Local search over an Obsidian vault. The vault is the source of truth. A local
SQLite file is derived memory: FTS5 (BM25) always, plus sqlite-vec when a local
Model2Vec snapshot is present. The skill makes zero LLM calls. Note text is not
sent to any remote model API.

```mermaid
flowchart TB
    subgraph harness["Agent harness"]
        AGENT["Coding agent<br/>reads SKILL.md, runs one launcher"]
    end

    subgraph skill["This folder: fde-kb/"]
        SKILL["SKILL.md"]
        LAUNCH["scripts/fde-kb(.cmd/.ps1)"]
        CORE["scripts/fde_kb.py"]
        ASSETS["assets/: schemas, templates"]
    end

    VAULT[("Obsidian vault")]
    DB[("index.sqlite<br/>derived, disposable")]

    AGENT --> SKILL --> LAUNCH --> CORE
    CORE --> ASSETS
    CORE --> VAULT
    CORE --> DB

    click AGENT "../SKILL.md" "What the agent is told to run"
    click SKILL "../SKILL.md" "SKILL.md"
    click LAUNCH "../scripts/fde-kb.ps1" "PowerShell launcher"
    click CORE "../scripts/fde_kb.py" "fde_kb.py"
    click ASSETS "../assets/schemas/note.schema.json" "Note schema"
    click VAULT "../assets/schemas/note.schema.json" "Frontmatter schema"
    click DB "../README.md" "Index path and FDE_KB_DB"
```

## Retrieval

The SQLite file is disposable: delete it and `index` rebuilds it. Two retrievers
run over the same database; rankings are merged with Reciprocal Rank Fusion.

```mermaid
flowchart TB
    VAULT[("Obsidian vault")]

    subgraph ix["index"]
        WALK["iter_markdown"]
        HASH["file_sha256"]
        CHUNK["chunk_markdown"]
        VALID["note_schema_errors"]
        EMBED["get_embedder"]
    end

    DB[("index.sqlite")]

    subgraph sx["search"]
        Q["query"]
        FTS["_lexical_ids"]
        VEC["_semantic_ids"]
        RRF["rrf_fuse"]
        FILTER["tag / type / since"]
    end

    OUT["JSON results"]

    VAULT --> WALK --> HASH --> CHUNK --> EMBED --> DB
    CHUNK --> VALID
    Q --> FTS --> RRF
    Q --> VEC --> RRF
    DB --> FTS
    DB --> VEC
    RRF --> FILTER --> OUT

    click VAULT "../assets/schemas/note.schema.json" "Note schema"
    click WALK "../scripts/fde_kb.py" "iter_markdown"
    click HASH "../scripts/fde_kb.py" "file_sha256"
    click CHUNK "../scripts/fde_kb.py" "chunk_markdown"
    click VALID "../assets/schemas/note.schema.json" "note.schema.json"
    click EMBED "../assets/models/README.md" "Model snapshot"
    click DB "../README.md" "FDE_KB_DB"
    click FTS "../scripts/fde_kb.py" "_lexical_ids"
    click VEC "../scripts/fde_kb.py" "_semantic_ids"
    click RRF "../scripts/fde_kb.py" "rrf_fuse"
    click FILTER "../scripts/fde_kb.py" "search"
    click OUT "../SKILL.md" "How the agent uses the JSON"
```

Keyword search wins on exact terms. Vector search wins on paraphrase. RRF only
uses ranks, so the two score scales do not need calibration. Without a local
model snapshot, search stays lexical and reports `"mode": "lexical"` with
`"degraded": true`.

## Writing notes

The agent picks type, title, and tags. The skill renders a schema-valid note.
People do not hand-edit frontmatter.

```mermaid
flowchart LR
    HUMAN["save this to the KB"]
    A["Agent picks type, title, tags"]
    SHORT["ingest --body"]
    LONG["ingest --body-file"]
    IMP["import PATH"]
    TPL["templates"]
    CLI{"Obsidian CLI?"}
    OB["obsidian create"]
    DISK["write on disk"]
    REIDX["index_file"]

    HUMAN --> A
    A --> SHORT --> TPL
    A --> LONG --> TPL
    A --> IMP --> TPL
    TPL --> CLI
    CLI -->|yes| OB --> REIDX
    CLI -->|no| DISK --> REIDX

    click A "../SKILL.md" "Writing to the vault"
    click SHORT "../scripts/fde_kb.py" "ingest"
    click LONG "../scripts/fde_kb.py" "read_body_source"
    click IMP "../scripts/fde_kb.py" "import_note"
    click TPL "../assets/templates/playbook.md" "playbook template"
    click REIDX "../scripts/fde_kb.py" "index_file"
```

`--body` is limited by the OS command-line length (8191 characters on Windows
`cmd`). Longer text uses `--body-file` or stdin.

## Launchers

```mermaid
flowchart TB
    CALL["launcher"]
    OS{"platform"}
    CMD["fde-kb.cmd"]
    BASH["fde-kb"]
    PS["fde-kb.ps1"]
    ENVF["load .env"]
    G1{"UV_DEFAULT_INDEX or FDE_KB_UV_INDEX?"}
    F1["stderr + exit 1"]
    UV["uv run --script"]
    G2{"local model?"}
    LEX["lexical"]
    HYB["hybrid"]

    CALL --> OS
    OS -->|Windows| CMD --> PS
    OS -->|Unix| BASH
    PS --> ENVF
    BASH --> ENVF
    ENVF --> G1
    G1 -->|no| F1
    G1 -->|yes| UV --> G2
    G2 -->|no| LEX
    G2 -->|yes| HYB

    click CMD "../scripts/fde-kb.cmd" "fde-kb.cmd"
    click BASH "../scripts/fde-kb" "bash launcher"
    click PS "../scripts/fde-kb.ps1" "fde-kb.ps1"
    click F1 "../scripts/fde-kb.ps1" "Ensure-UvIndex"
    click G2 "../assets/models/README.md" "Model snapshot"
    click LEX "../README.md" "Setup"
```

Set `FDE_KB_UV_INDEX` (or `UV_DEFAULT_INDEX`) to your package index before first
run. Development only: `FDE_KB_ALLOW_PUBLIC_INDEX=1`.

## Index location

`chunks.text` stores note text in the SQLite file. Default paths:

- Windows: `%LOCALAPPDATA%\fde-kb\index.sqlite`
- macOS / Linux: `~/.cache/fde-kb/index.sqlite`

Override with `FDE_KB_DB` if you want the index next to the vault.

## Design choices

| Choice | Why |
|---|---|
| SQLite | One file, no service |
| Static embeddings | Small, CPU-only |
| Weights not in this folder | Keep the skill copy small and reviewable |
| RRF over raw scores | No score calibration |
| Zero LLM calls in the skill | Leaf tool; rewriting stays in the agent |
| Chunk on headings | Matches how notes are written |

## File index

| Area | File |
|---|---|
| Agent instructions | [SKILL.md](../SKILL.md) |
| Operator README | [README.md](../README.md) |
| Implementation | [scripts/fde_kb.py](../scripts/fde_kb.py) |
| Launchers | [fde-kb](../scripts/fde-kb) · [fde-kb.cmd](../scripts/fde-kb.cmd) · [fde-kb.ps1](../scripts/fde-kb.ps1) |
| Note schema | [note.schema.json](../assets/schemas/note.schema.json) |
| Golden schema | [golden-case.schema.json](../assets/schemas/golden-case.schema.json) |
| Templates | [assets/templates/](../assets/templates/) |
| Model placement | [assets/models/README.md](../assets/models/README.md) |
| Test vault | [scripts/make-test-vault.py](../scripts/make-test-vault.py) |
