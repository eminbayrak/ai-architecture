# Poolside skill discovery

Poolside scans `.poolside/skills/`, not `skills/`. Git symlinks do not survive a
normal Windows clone (no Developer Mode, no elevation). This folder holds
generated directory junctions (Windows) or symlinks (macOS / Linux).

After a clone:

```bat
py -3 scripts\link-skills.py
```

Or: `scripts\link-skills.cmd`

```bash
python scripts/link-skills.py
python scripts/link-skills.py --list
python scripts/link-skills.py --skills fde-kb
python scripts/link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
```

Each skill folder is self-contained. `--skills` installs only those ids.

If an existing clone already has a **file** named `fde-kb`, `jira`,
`graph-memory`, `llm-wiki`, or `retrieval-bench` whose
contents are `../../skills/...`, that is the broken git-symlink checkout.
Delete those files and rerun the script. Do not enable Developer Mode for this.

When copying only `skills/fde-kb/` into `.poolside/skills/fde-kb`, use a real
directory (or this link script). Do not leave a plain-text placeholder file.
