# Wiki schema

Conventions the agent follows on ingest. The CLI does not enforce prose quality.

## Layers

- `raw/` immutable sources. The CLI never writes here.
- `wiki/` compiled pages. Humans read; the agent writes; `query` reads.
- this file plus `SKILL.md` are the schema.

## Required files

- `wiki/index.md` catalog by category. One line per page. Not the answer payload.
- `wiki/log.md` append-only. Prefix: `## [YYYY-MM-DD] ingest | Title`

## Page shape

```markdown
# Full display name

One-paragraph summary. Use the full name (Tomasz Krol), not only a slug.

## Aliases

- FD
- Toma

## Links

- [[nora-hale|Nora Hale]] (covers)

## Sources

- nora-ooo-august.md
```

## Rules

- Extract only facts stated in `raw/`.
- When a new source contradicts an old page, keep both and mark the old one superseded.
- Put amounts and dates in the summary, not only in a table no one opens.
- Do not add "boss" or other words the source never used.
