# Graph memory skill

A knowledge graph the assistant reads before it answers. Three SQLite tables, one recursive query, one CLI. No server, no API key, no model call in the walker.

This is the push path. Hybrid RAG stays in `skills/fde-kb` (the pull path). Do not merge them.

## Which one

    fde-kb          notes as they are, keyword + optional vectors, RRF
    graph-memory    modelled docs, typed links, multi-hop answers walked by code
    llm-wiki        compiled markdown pages; agent ingest; CLI query

Each folder installs alone. This skill does not import llm-wiki. `compare` still needs sibling fde-kb.

## Prerequisites

Python 3.12+ (SQLite included). Nothing else to install for `build` / `recall`.
`compare` also needs the sibling `fde-kb` skill in this kit.

## Layout

    corpus/            8 modelled docs (front matter)
    corpus-before/     12 unstructured docs (the A/B control)
    extraction/        LLM output per doc: nodes, edges, aliases
    src/               schema.sql, build_graph.py, recall.py, recall_hook.py
    scripts/           graph-memory launcher (build | recall | compare)
    hooks.json         optional UserPromptSubmit adapter; not required
    extract-prompt.md  the extraction prompt, for your own docs

## Modelling

Ontology: a closed vocabulary, fixed before writing any doc.
Entity types: PERSON, ROLE, POLICY, PROCESS, DOCUMENT.
Relationships: approved_by, held_by, delegates_to, part_of, references.
Logical model: entities with hashed identity (uuid5 of type + name), typed
relations carrying their source doc, aliases for name variants.

The walker does not call a model. Any model that can read text can use the output.

## Run

```bash
./scripts/graph-memory build
./scripts/graph-memory recall "A customer wants an £800 refund in March. Who signs it off?"
./scripts/graph-memory compare
```

Windows: `scripts\graph-memory.cmd`.

## Poolside

After clone, link into `.poolside/skills/` (Poolside does not read `skills/` directly):

```bat
py -3 scripts\link-skills.py --skills graph-memory
```

```bash
python3 scripts/link-skills.py --skills graph-memory
```

Or copy `skills/graph-memory/` to `.poolside/skills/graph-memory/` as a real directory.

## Test cases

| Question | Expected | Why |
|---|---|---|
| A customer wants an £800 refund in March. Who signs it off? | Marcus Webb | 3 hops: policy, role, holder, delegate |
| Who approves supplier payments over £2,000? | Alex Doyle | 2 hops: policy, role, holder |
| What is the onboarding process? | Ops Manager, day-one checklist | 1 hop, several edges |
| What does Priya do? | Support Lead | alias seeding |
| Who is in charge when the boss is away? | no memory matches | outside the vocabulary; fails loudly, never guesses |

## Two ways to prove

Automated (no live model): `uv run pytest skills/graph-memory/tests`.
`compare` indexes `corpus-before` with fde-kb and recalls the same question from the graph.
Pull surfaces the £500 rule. Push walks to Marcus Webb.

Manual: pick any model your harness already exposes. Ask the refund question with no recall, then run `recall` and ask again with the printed facts in context.

## Optional hook

`hooks.json` is one harness adapter. Copy it only if you want submit-time injection in that harness. The CLI is the portable interface.

Default command: `scripts/graph-memory hook` (macOS / Linux, or Windows with Git Bash).

On Windows without Bash, edit `hooks.json` to: `scripts\\graph-memory.cmd hook`.

## Your own docs

Run the prompt in `extract-prompt.md` over a document. Save JSON under `extraction/`. Rebuild. This skill does not call a model to extract.
