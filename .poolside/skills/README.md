# Poolside skill discovery

Poolside scans `.poolside/skills/`, not `skills/`. Git symlinks do not survive a
normal Windows clone (no Developer Mode, no elevation). This folder holds
generated directory junctions (Windows) or symlinks (macOS / Linux).

After a clone:

```bat
py -3 scripts\link-skills.py
```

```bash
python scripts/link-skills.py
```

If an existing clone already has a **file** named `fde-kb` or `jira` whose
contents are `../../skills/...`, that is the broken git-symlink checkout.
Delete those files and rerun the script. Do not enable Developer Mode for this.

When copying only `skills/fde-kb/` into `.poolside/skills/fde-kb`, use a real
directory (or this link script). Do not leave a plain-text placeholder file.
