# Retrieval bench

One command. Writes a local HTML scorecard for `graph-memory`, `fde-kb`, and `llm-wiki`, then **opens it in your browser**.

**No model API key.** These are harness skills. Poolside / Codex / Claude Code / Cursor do ingest when the skill asks. This CLI only indexes and retrieves (or uses script `compile-extracts` as a stand-in for llm-wiki pages). Pass `--wiki` to score a wiki the agent already wrote.

**Output stays out of the git repo:** `%TEMP%\retrieval-bench\run-*` (Windows) or `$TMPDIR/retrieval-bench/run-*` (macOS).

Windows and macOS/Linux. Python 3.12+. `git` only for GitHub URLs.

---

## 1. Multi-hop demo (refund / delegation) — use this to see graph-memory win

Uses `skills/graph-memory/corpus-before` (messy notes for fde-kb) plus the real modelled `extraction/*.json` (typed hops for graph-memory). Fixed push questions, including the £800 March refund.

```bat
py -3 scripts\link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
py -3 scripts\retrieval-bench.py --demo multihop
```

```bash
cd /path/to/fde-lab
python3 scripts/link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
uv run python3 scripts/retrieval-bench.py --demo multihop
```

Same story without the HTML report (CLI only):

```bash
./skills/graph-memory/scripts/graph-memory build
./skills/graph-memory/scripts/graph-memory recall "A customer wants an £800 refund in March. Who signs it off?"
./skills/graph-memory/scripts/graph-memory compare
```

---

## 2. Any GitHub repo (auto questions — mostly “find the doc”)

```bat
py -3 scripts\retrieval-bench.py --repo https://github.com/org/private.git
```

```bash
uv run python3 scripts/retrieval-bench.py --repo https://github.com/org/repo
```

Private repos: sign in first (`gh auth login` or Git Credential Manager).

Score a wiki Poolside already ingested:

```bash
uv run python3 scripts/retrieval-bench.py --repo /path/to/sources --wiki /path/to/wiki
```

---

## 3. Local checkout

```bat
py -3 scripts\retrieval-bench.py --repo C:\path\to\checkout
```

```bash
python3 scripts/retrieval-bench.py --repo /path/to/checkout
```

---

## 4. Obsidian / fde-kb vault

```bat
py -3 scripts\retrieval-bench.py --repo C:\vaults\FDE-vault
```

```bash
python3 scripts/retrieval-bench.py --repo ~/vaults/FDE-vault
```

---

## Flags

| Flag | Purpose |
|------|---------|
| `--demo multihop` | Built-in refund / delegation report (no `--repo`) |
| `--repo` | GitHub URL or local folder (repo, vault, checkout) |
| `--wiki path` | Score an existing `wiki/` the harness agent wrote |
| `--no-open` | Skip opening the report (default: open) |
| `--out DIR` | Override output folder (default: OS temp) |
| `--questions path.json` | Your own `{question, needles, empty_ok, reason}` list |
| `--window 128000` | Context window for token bars |

## Poolside

Skills live under `.poolside/skills/` after `link-skills.py`. The agent follows each `SKILL.md`. Env for vault paths (`FDE_KB_VAULT`) lives at the **repo root**. No separate OpenAI key for these retrieval skills.
