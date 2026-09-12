# Prompt: rework the stuck-part finder skill

Temporary file. Delete after transfer.

## Context for the assistant

I built an agent skill that finds parts stuck in a repair and inventory pipeline.
A design review rejected the current approach. This prompt tells you what to change
and why. Read the whole file before you edit anything.

## What is wrong with the skill today

1. The skill relies on generated SQL. A code assistant wrote queries against warehouse
   tables that nobody on my side understands. Nobody reviewed the joins or the filters.
2. The skill has no documented business process behind it. I do not know what the user
   does after the skill returns a stuck part.
3. The skill mixes two different things. It reads data and it also invents the data
   access layer. Those must separate.
4. The skill queries raw or unknown tables. It should query curated output tables from
   the transformation pipeline instead.

## The target design

The skill is a thin wrapper over fixed, reviewed queries. It is not a SQL generator.

1. Every query is a static, version-controlled artifact in the repo.
2. A query accepts a small, named set of filter arguments. Nothing else varies.
3. One skill entry point maps to one data concept. Do not build one entry point that
   answers every question.
4. Each entry point returns a small, filtered record set. It never returns a full table.
   The record set becomes part of the model context.
5. If a curated pipeline job already produces the answer, call its output table. Do not
   rebuild the logic.

## Tasks

Work in this order. Stop and tell me if a task cannot be done with what is in the repo.

### 1. Audit and label every query

For each SQL statement in the skill:

1. Print the statement.
2. State whether a human wrote it or an assistant generated it. Use repo history and
   comments as evidence. Say "unknown" when you cannot tell.
3. List every table it touches.
4. State whether each table is a curated output table or a raw ingested table.
5. Flag any join whose key relationship you cannot confirm from the schema.

Write the result to a file named `query-audit.md`. Do not change any SQL in this task.

### 2. Split the skill into data-access units

1. Propose one unit per data concept the skill needs. Name each unit.
2. For each unit, define the input filters and the output columns.
3. Each unit holds exactly one reviewed query.
4. Keep the reasoning and the presentation logic out of these units.

Write the proposal to `skill-decomposition.md`. Do not write code yet.

### 3. Mark every unverified query

1. Add a header comment to every query you cannot confirm against the schema.
2. The comment states that a domain expert must review the query before use.
3. Add a runtime guard. The skill refuses to return results from an unverified query
   unless the caller passes an explicit override flag.

### 4. Remove dynamic SQL generation

1. Find every code path where the skill builds SQL text at runtime.
2. Replace each path with a call to a stored, parameterized query.
3. If a path cannot be replaced, list it and explain what input drives the variation.

### 5. Write the open questions

Produce `open-questions.md`. Include every question I must ask the domain expert
before the skill is correct. Base the questions on gaps you found, not on guesses.

Cover at minimum:

1. What defines a stuck part. Name the exact field and threshold.
2. Which curated output table holds the authoritative current state.
3. Which table records the date a part arrived at its current location.
4. What the user does with the result. Who receives it. In what format.
5. Which step comes before this task and which step comes after it.
6. Whether an existing report or job already answers this question.

## Rules

1. Do not invent table names. Do not invent column names.
2. Do not write a query against a table you cannot find in the catalog.
3. When the schema is unclear, write a question in `open-questions.md`. Do not guess.
4. Do not expand the scope. Fix what exists.
5. Do not add abstractions for cases that cannot happen.
6. Prefer deleting code over adding code.

## Definition of done

1. The skill contains zero runtime SQL generation.
2. Every query has an author label and a review status.
3. Every unverified query is blocked behind an explicit override.
4. The three markdown files exist and are complete.
