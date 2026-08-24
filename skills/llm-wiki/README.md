# llm-wiki skill

A compiled markdown wiki. Raw notes stay in `raw/`. The agent writes `wiki/`. The CLI searches and lints those pages. No server, no API key, no model call in `query` / `lint` / `compile-extracts`.

This folder is the whole skill. Copy only `skills/llm-wiki` into `.poolside/skills/llm-wiki` if that is the path you want. You do not need fde-kb or graph-memory installed.

## Which skill

    fde-kb          search notes as they are (pull RAG)
    graph-memory    typed triples, SQLite walk (push recall)
    llm-wiki        interlinked pages, compile once, query the wiki

Pick one folder. Or install several. They do not import each other.

## Prerequisites

Python 3.12+ (stdlib). Nothing else for `query` / `lint` / `status`.

## Run

```bash
./scripts/llm-wiki query "A customer wants a £12,000 credit in August. Who signs?"
./scripts/llm-wiki lint
./scripts/llm-wiki status
```

Windows: `scripts\llm-wiki.cmd`.

Optional: turn graph-memory `extraction/*.json` into pages without a model:

```bash
./scripts/llm-wiki compile-extracts /path/to/extraction --wiki /tmp/wiki
```

Ingest of new sources is the agent following [SKILL.md](SKILL.md) and [schema.md](schema.md). Write full names on pages. Slugs alone hide answers from later search.

## Demo questions (shipped wiki)

| Question | Expected |
|---|---|
| £12,000 credit in August. Who signs? | Tomasz Krol |
| What is the mileage rate? | 45p |
| Who is in charge when the boss is away? | no wiki pages |

## Benchmark

Open [docs/benchmark.html](docs/benchmark.html) in a browser. Same report lives at repo `docs/retrieval-benchmark.html`. Against another repo: `py -3 scripts/retrieval-bench.py --repo PATH`.
