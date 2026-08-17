#!/usr/bin/env python3
"""Generate a throwaway vault for exercising the fde-kb skill.

The content is deliberately generic engineering-practice material. It is not a
sample corpus for the skill (the skill ships none on purpose) and it must never
be pointed at, copied into, or merged with a real vault.

Usage::

    python skills/fde-kb/scripts/make-test-vault.py --dest /tmp/kb-test-vault
    python .poolside/skills/fde-kb/scripts/make-test-vault.py --dest C:/temp/kb-test-vault

Then::

    set FDE_KB_VAULT=C:/temp/kb-test-vault
    set FDE_KB_DB=C:/temp/kb-test.sqlite
    .poolside/skills/fde-kb/scripts/fde-kb.cmd index
    .poolside/skills/fde-kb/scripts/fde-kb.cmd eval

Every note satisfies assets/schemas/note.schema.json (in this skill) and lives in
the folder its type requires. Golden queries are paraphrases rather than
keyword matches, so lexical and hybrid retrieval score differently.

This proves the pipeline works. It does not measure retrieval quality: six
notes against the default k=8 means recall cannot fail. Use `eval -k 1` for a
signal that can move, and read any number here as plumbing verification only.
Real measurement needs real questions against the real vault.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MARKER = ".fde-kb-test-vault"

NOTES: list[tuple[str, str, list[str], str]] = [
    (
        "playbooks/latency-budgets.md",
        "Agree a latency budget before choosing a model",
        ["latency", "scoping", "serving"],
        """Settle the p95 response time with the customer before anyone selects a model.

## Why the order matters

Retrofitting a response time target after the model is chosen usually means
changing the model rather than tuning the serving stack. That is a much harder
conversation once the customer has already seen demo quality and formed an
expectation about answer depth.

## What to write down

Record the target, the percentile it applies to, and whether it covers cold
start. A budget that only holds in steady state is not a budget, because the
first request after every deployment will miss it.

## Typical failure

Teams measure on a warm process on a developer laptop, then discover that
container cold start alone consumes most of the allowance in production.
""",
    ),
    (
        "playbooks/eval-before-code.md",
        "Write the evaluation set before writing the pipeline",
        ["evaluation", "method", "quality"],
        """Collect the questions you intend to answer well before building anything
that answers them.

## The rule

Thirty to fifty real questions, each paired with the document or record that
should satisfy it, written by somebody who actually did the work. Fabricated
questions produce a system tuned for fabricated questions.

## Why teams skip it

Producing the pairs is slow and unglamorous, and the pipeline feels like
progress. The cost of skipping shows up later as an inability to tell whether a
change helped, which turns every subsequent decision into an argument about
taste.

## Signals you skipped it

Nobody can say what the current accuracy is, and every proposed improvement is
justified by an anecdote about one query somebody tried yesterday.
""",
    ),
    (
        "playbooks/keeping-inference-on-the-machine.md",
        "Keeping inference local when material cannot leave the boundary",
        ["privacy", "architecture", "offline"],
        """When source material is restricted, the deciding constraint is where
computation happens, not which model is best.

## The constraint reframed

A hosted model is disqualified by the fact that content crosses a boundary, not
by its accuracy. Comparing a hosted model favourably against a local one on
quality misses the point entirely, because the hosted option was never eligible.

## Practical consequences

Small static embedding models run acceptably on a laptop processor with no
accelerator. Keyword retrieval needs no model at all. Combining the two gets
most of the benefit without any request leaving the host.

## Derived copies are the trap

An index built from restricted material is itself restricted material. Storing
it in a user profile directory silently moves a readable copy outside whatever
access rules protected the original.
""",
    ),
    (
        "playbooks/running-a-discovery-workshop.md",
        "Running a discovery workshop that produces decisions",
        ["discovery", "scoping", "facilitation"],
        """A discovery session should end with written decisions and named owners,
not with a summary of what everyone already knew.

## Preparation

Send three questions in advance and ask for written answers. People who write
before the meeting arrive with positions, and positions can be reconciled.
Meetings that begin with open discovery usually end with a follow-up meeting.

## During the session

Timebox the current-state review hard. Most of the value comes from the
disagreements, and those only surface once someone proposes something concrete
enough to object to.

## The output

One page: the decision taken, the alternatives rejected, the reason, and who
owns the next step. Circulate it the same day while people still remember
agreeing.
""",
    ),
    (
        "engagements/internal-tooling-pilot-retro.md",
        "Retrospective: internal tooling pilot",
        ["retro", "adoption", "pilot"],
        """A four week pilot of an internal assistant with a group of eight
engineers.

## What worked

Shipping a narrow tool that did one retrieval task well produced more adoption
than the previous broad assistant, which did many things adequately. Users
formed a reliable mental model of what it was for.

## What did not

Content supply, not retrieval accuracy, limited usefulness. The knowledge base
launched with fourteen documents and the team assumed contributions would
follow naturally. They did not, because writing documentation was nobody's
priority and the tool gave no reason to change that.

## What we would change

Harvest from work that already produces text, such as ticket histories and
meeting recordings, instead of asking busy people to author from a blank page.
""",
    ),
    (
        "evals/retrieval-baseline-method.md",
        "How we measure retrieval quality",
        ["evaluation", "metrics", "method"],
        """Recall at k and mean reciprocal rank, computed against a fixed set of
question and document pairs.

## Definitions

Recall at k asks whether the correct document appeared anywhere in the first k
results. Mean reciprocal rank rewards putting it near the top. A system can
have strong recall and weak reciprocal rank, which feels bad to use because the
right answer is on the page but buried.

## Interpreting a comparison

Report keyword-only, vector-only, and fused numbers separately. If fusion does
not beat both halves on reciprocal rank, the fusion is not earning the
complexity it adds.

## Honest reporting

State the corpus size alongside every number. Retrieval scores from a corpus of
forty documents say almost nothing about behaviour at several thousand.
""",
    ),
]

# Paraphrases on purpose: none of these repeat the note's distinctive wording.
GOLDEN: list[tuple[str, str]] = [
    ("when should we pin down response time targets", "playbooks/latency-budgets.md"),
    ("the bill for slowness comes due after deployment", "playbooks/latency-budgets.md"),
    ("do we build the pipeline or the test questions first", "playbooks/eval-before-code.md"),
    ("how do we know if a change actually improved anything", "playbooks/eval-before-code.md"),
    ("material that is not allowed to leave the building", "playbooks/keeping-inference-on-the-machine.md"),
    ("does the search database inherit the original access rules", "playbooks/keeping-inference-on-the-machine.md"),
    ("how do we stop workshops ending in another workshop", "playbooks/running-a-discovery-workshop.md"),
    ("why did people stop using the assistant we shipped", "engagements/internal-tooling-pilot-retro.md"),
    ("nobody is contributing documents to the knowledge base", "engagements/internal-tooling-pilot-retro.md"),
    ("what does it mean when the answer is on page one but low down", "evals/retrieval-baseline-method.md"),
]


def render(title: str, note_type: str, tags: list[str], body: str) -> str:
    return f"---\ntitle: {title}\ntype: {note_type}\ntags: [{', '.join(tags)}]\n---\n# {title}\n\n{body}"


def guard(dest: Path, force: bool) -> None:
    if not dest.exists():
        return
    if (dest / MARKER).is_file():
        return
    existing = [p for p in dest.rglob("*.md")]
    if not existing:
        return
    if force:
        return
    raise SystemExit(
        f"refusing to write into {dest}: it already holds {len(existing)} markdown "
        f"files and has no {MARKER} marker. This script is for throwaway vaults "
        "only. Pick an empty directory, or pass --force if you are certain."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a throwaway fde-kb test vault.")
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    dest = args.dest.expanduser().resolve()
    guard(dest, args.force)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / MARKER).write_text(
        "Generated by skills/fde-kb/scripts/make-test-vault.py. Throwaway test data. Do not merge "
        "into a real vault.\n",
        encoding="utf-8",
    )

    for rel, title, tags, body in NOTES:
        note_type = {"playbooks": "playbook", "engagements": "engagement", "evals": "eval"}[
            rel.split("/", 1)[0]
        ]
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(title, note_type, tags, body), encoding="utf-8")
        print(f"wrote {rel}")

    golden = dest / "evals" / "golden.jsonl"
    golden.parent.mkdir(parents=True, exist_ok=True)
    with golden.open("w", encoding="utf-8") as fh:
        for query, path in GOLDEN:
            fh.write(json.dumps({"query": query, "path": path}) + "\n")
    print(f"wrote evals/golden.jsonl ({len(GOLDEN)} cases)")

    print(f"\ntest vault ready: {dest}")
    print("set FDE_KB_VAULT to that path and FDE_KB_DB to a scratch file, then run index.")
    print("for eval, use -k 1: six notes against the default k=8 cannot fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
