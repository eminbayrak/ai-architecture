# graph-memory demo walkthrough

Prove pull search vs push recall on the planted refund question. No live vendor model required.

Time: about 10 minutes.

## What you need

- This skill folder at `skills/graph-memory/` (or linked under `.poolside/skills/`)
- Sibling `skills/fde-kb`
- Python 3.12+

## 1. Build and recall (terminal)

From the repo root:

```bash
./skills/graph-memory/scripts/graph-memory build
./skills/graph-memory/scripts/graph-memory recall "A customer wants an £800 refund in March. Who signs it off?"
```

Call out: `Sarah Chen --[delegates_to]--> Marcus Webb`, plus a `where:` note that Sarah is on leave in March. Retrieval is a SQLite walk. No model ran.

## 2. Compare pull vs push

```bash
./skills/graph-memory/scripts/graph-memory compare
```

Call out:

- `rag.results` hit the handbook / £500 rule (and maybe the superseded 2024 decoy)
- `rag.blob` does not contain the structured triple chain
- `graph.text` does contain `Sarah Chen --[delegates_to]--> Marcus Webb`

That is the kit-local proof: pull finds evidence; push walks hops.

## 3. Fail loud

```bash
./skills/graph-memory/scripts/graph-memory recall "Who is in charge when the boss is away?"
```

Expect `(no memory matches for this prompt)`. "boss" is outside the vocabulary.

## 4. Any model (optional)

Pick whatever model the harness already has. Ask the refund question two ways:

1. No recall. Let the model search or guess over `corpus-before`.
2. Paste or inject the `recall` output, then ask again.

Do not switch models between the two runs. The point is the facts, not the model.

## Cleanup

Delete `graph.db` and any `--work` directory from `compare`. Do not commit them.
