# Optional Model2Vec snapshot

Do not put `model.safetensors` (or other weight files) in git.

Day-0 search uses lexical FTS5. Hybrid / semantic embeddings load only from a
machine-local copy of the approved `minishlab/potion-base-8M` snapshot
(revision `bf8b056651a2c21b8d2565580b8569da283cab23`):

- `FDE_KB_MODEL` (directory with `config.json` and `model.safetensors`), or
- `~/.cache/fde-kb/models/potion-base-8M` (Windows: `%LOCALAPPDATA%\fde-kb\models\potion-base-8M`)

Ops can drop the approved files there. Hugging Face is not contacted.
After placing files, run `fde-kb index --force`.
