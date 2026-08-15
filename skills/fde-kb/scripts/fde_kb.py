# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "sqlite-vec",
#   "model2vec",
# ]
# ///
"""FDE knowledge base: Obsidian vault + SQLite hybrid RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EMBED_DIM = 256
EMBED_MODEL = "minishlab/potion-base-8M"
EMBED_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"
HASH_MODEL = "hash-256"
SCHEMA_VERSION = "3"
CHUNKER_VERSION = "2"
RRF_K = 60
MIN_CHUNK_CHARS = 30
MAX_CHUNK_CHARS = 2000
CHUNK_OVERLAP = 200
SNIPPET_CHARS = 400
MAX_PER_NOTE = 2
SLUG_MAX = 80
WIN_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *{f"com{i}" for i in range(1, 10)},
    *{f"lpt{i}" for i in range(1, 10)},
}
SKIP_HEADINGS = {"related", "see also", "links", "references"}
SKIP_DIR_NAMES = {".obsidian", ".trash", ".git"}
TYPE_TO_DIR = {
    "playbook": "playbooks",
    "engagement": "engagements",
    "eval": "evals",
}
SKILL_ROOT = Path(__file__).resolve().parent.parent
NOTE_SCHEMA_PATH = SKILL_ROOT / "assets" / "schemas" / "note.schema.json"
GOLDEN_SCHEMA_PATH = SKILL_ROOT / "assets" / "schemas" / "golden-case.schema.json"
_SCHEMA_CACHE: dict[str, dict] = {}
MISSING_VAULT_HINT = (
    "Set FDE_KB_VAULT to the Obsidian vault directory (absolute path). "
    "Optional: FDE_KB_VAULT_NAME for the CLI vault= parameter."
)
MISSING_OBSIDIAN_HINT = (
    "obsidian CLI not found. In Obsidian: Settings → General → enable "
    "Command line interface. Installer 1.12.4+."
)
SANDBOX_HINT = (
    "Poolside sandboxes do not mount the default index dir (~/.cache/fde-kb or "
    "%LOCALAPPDATA%\\fde-kb) or a vault outside the workspace unless you add "
    "an extra mount or run unsandboxed."
)
MISSING_GOLDEN_HINT = (
    "golden set not found. Pass --golden PATH, set FDE_KB_GOLDEN, or put "
    "evals/golden.jsonl in the vault. Each line must match "
    "assets/schemas/golden-case.schema.json (query + path)."
)
UV_INDEX_HINT = (
    "fde-kb: FDE_KB_UV_INDEX / UV_DEFAULT_INDEX is not set. "
    "Point it at the internal package index (sqlite-vec, model2vec). "
    "Public PyPI is not used. Development only: FDE_KB_ALLOW_PUBLIC_INDEX=1."
)
_DOTENV_EXTRA_KEYS = frozenset(
    {"UV_DEFAULT_INDEX", "UV_INDEX_URL", "HF_HOME", "HF_HUB_CACHE"}
)
PATH_OUTSIDE = "path is outside the vault"
OBSIDIAN_PROBE_TIMEOUT = 2

try:
    import sqlite_vec
except ImportError:
    sqlite_vec = None  # type: ignore[assignment]


class KbConnection(sqlite3.Connection):
    vec_enabled: bool = False
    vec_degraded: bool = False


def _vec_on(conn: sqlite3.Connection) -> bool:
    return bool(getattr(conn, "vec_enabled", False))


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


@dataclass
class Chunk:
    heading: str
    text: str
    tags: list[str] = field(default_factory=list)


class HashEmbedder:
    """Test/CI embedder. Production uses Model2VecEmbedder."""

    model_id = HASH_MODEL
    revision = "local"
    dim = EMBED_DIM

    def encode(self, texts: list[str] | str) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * EMBED_DIM
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:2], "big") % EMBED_DIM
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


FakeEmbedder = HashEmbedder


def allow_public_index() -> bool:
    return (os.environ.get("FDE_KB_ALLOW_PUBLIC_INDEX") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _model_dir_ready(path: Path) -> bool:
    return path.is_dir() and (path / "config.json").is_file()


def cache_root(env: dict[str, str] | os._Environ | None = None) -> Path:
    env = env or os.environ
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "fde-kb"
    cache = env.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "fde-kb"


def cache_model_dir(env: dict[str, str] | os._Environ | None = None) -> Path:
    return cache_root(env) / "models" / "potion-base-8M"


def hf_snapshot_dir() -> Path:
    cache = (os.environ.get("HF_HUB_CACHE") or "").strip()
    if cache:
        hub = Path(cache).expanduser()
    else:
        home = (os.environ.get("HF_HOME") or "").strip()
        hub = (
            Path(home).expanduser() / "hub"
            if home
            else Path.home() / ".cache" / "huggingface" / "hub"
        )
    repo = "models--" + EMBED_MODEL.replace("/", "--")
    return hub / repo / "snapshots" / EMBED_REVISION


def _looked_model_path() -> Path:
    explicit = (os.environ.get("FDE_KB_MODEL") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return cache_model_dir()


def missing_model_message(looked: Path) -> str:
    return (
        f"fde-kb: pinned model {EMBED_MODEL} revision {EMBED_REVISION} is absent at {looked}. "
        "Indexing and search use lexical FTS5. To enable hybrid, place the approved snapshot "
        f"(config.json + model.safetensors) at {cache_model_dir()} or set FDE_KB_MODEL. "
        "Hugging Face is not contacted."
    )


def find_model_dir() -> Path | None:
    explicit = (os.environ.get("FDE_KB_MODEL") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve() if _model_dir_ready(path) else None
    cache = cache_model_dir()
    if _model_dir_ready(cache):
        return cache.resolve()
    snap = hf_snapshot_dir()
    return snap.resolve() if _model_dir_ready(snap) else None


def resolve_model_dir() -> Path:
    found = find_model_dir()
    if found is not None:
        return found
    print(missing_model_message(_looked_model_path()), file=sys.stderr)
    raise SystemExit(1)


def resolved_uv_index() -> str | None:
    raw = (
        os.environ.get("UV_DEFAULT_INDEX") or os.environ.get("FDE_KB_UV_INDEX") or ""
    ).strip()
    return raw or None


class Model2VecEmbedder:
    model_id = EMBED_MODEL
    revision = EMBED_REVISION
    dim = EMBED_DIM

    def __init__(self, model_id: str = EMBED_MODEL, revision: str = EMBED_REVISION) -> None:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        self.model_id = EMBED_MODEL
        self.revision = EMBED_REVISION
        local = find_model_dir()
        if local is None:
            print(missing_model_message(_looked_model_path()), file=sys.stderr)
            raise SystemExit(1)
        try:
            from model2vec import StaticModel
        except ImportError:
            print(
                "fde-kb: model2vec is not installed. Point UV_DEFAULT_INDEX / "
                "FDE_KB_UV_INDEX at the internal index and rerun the launcher. "
                "Public PyPI is not used.",
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        self.model_path = str(local)
        self.model = StaticModel.from_pretrained(str(local))

    def encode(self, texts: list[str] | str) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        vecs = self.model.encode(texts)
        out: list[list[float]] = []
        for vec in vecs:
            if hasattr(vec, "tolist"):
                out.append([float(x) for x in vec.tolist()])
            else:
                out.append([float(x) for x in vec])
        return out


def _parse_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key not in os.environ and (
            key.startswith("FDE_KB_") or key in _DOTENV_EXTRA_KEYS
        ):
            os.environ[key] = value


def _dotenv_candidates() -> list[Path]:
    starts = [Path.cwd().resolve(), Path(__file__).resolve().parent]
    out: list[Path] = []
    seen: set[Path] = set()
    for start in starts:
        cur = start
        for _ in range(8):
            path = cur / ".env"
            resolved = path.resolve() if path.exists() else path
            if resolved not in seen:
                seen.add(resolved)
                out.append(path)
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
    return out


def load_dotenv() -> None:
    for path in _dotenv_candidates():
        if path.is_file():
            _parse_env_file(path)
            return


def pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def serialize_query(vec: list[float]) -> bytes:
    if sqlite_vec is not None:
        return sqlite_vec.serialize_float32(vec)
    return pack_vec(vec)


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n))) or 1.0
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n))) or 1.0
    return dot / (na * nb)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = (slug[:SLUG_MAX]).rstrip("-") or "note"
    if slug in WIN_RESERVED:
        slug = f"{slug}-note"
    return slug


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        return {}, text
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    block = rest[:end]
    body = rest[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    meta: dict[str, object] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if key == "tags":
            inner = raw.strip().removeprefix("[").removesuffix("]")
            meta["tags"] = [t.strip().strip("'").strip('"') for t in inner.split(",") if t.strip()]
        else:
            meta[key] = raw.strip("'").strip('"')
    return meta, body


def load_json_schema(path: Path) -> dict:
    key = str(path)
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None:
        return cached
    if not path.is_file():
        print(f"schema not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    schema = json.loads(path.read_text(encoding="utf-8"))
    _SCHEMA_CACHE[key] = schema
    return schema


def validate_instance(instance: object, schema: dict) -> list[str]:
    """Check instance against the shipped note/golden JSON Schema subset."""
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(instance, dict):
            return ["value is not an object"]
        required = schema.get("required") or []
        for key in required:
            if key not in instance:
                errors.append(f"missing {key}")
        props = schema.get("properties") or {}
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            spec = props.get(key)
            if spec is None:
                if additional is False:
                    errors.append(f"unknown field {key}")
                continue
            errors.extend(_validate_prop(key, value, spec))
        return errors
    return errors


def _validate_prop(key: str, value: object, spec: dict) -> list[str]:
    expected = spec.get("type")
    if expected == "string":
        if not isinstance(value, str):
            return [f"{key} must be a string"]
        min_len = spec.get("minLength")
        if isinstance(min_len, int) and len(value) < min_len:
            return [f"{key} is empty"]
        pattern = spec.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            return [f"{key} does not match {pattern}"]
        allowed = spec.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            return [f"{key} must be one of {allowed}"]
        return []
    if expected == "array":
        if not isinstance(value, list):
            return [f"{key} must be an array"]
        min_items = spec.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            return [f"{key} needs at least {min_items} item(s)"]
        item_spec = spec.get("items")
        errors: list[str] = []
        if isinstance(item_spec, dict):
            for i, item in enumerate(value):
                errors.extend(_validate_prop(f"{key}[{i}]", item, item_spec))
        return errors
    return []


def frontmatter_instance(meta: dict[str, object]) -> dict[str, object]:
    instance: dict[str, object] = {}
    if "title" in meta:
        instance["title"] = str(meta.get("title") or "").strip()
    if "type" in meta:
        instance["type"] = str(meta.get("type") or "").strip()
    if "tags" in meta:
        tags = meta.get("tags")
        instance["tags"] = [str(t) for t in tags] if isinstance(tags, list) else tags
    for key, value in meta.items():
        if key not in instance:
            instance[key] = value
    return instance


def note_schema_errors(meta: dict[str, object], rel_path: str = "") -> list[str]:
    instance = frontmatter_instance(meta)
    errors = validate_instance(instance, load_json_schema(NOTE_SCHEMA_PATH))
    note_type = instance.get("type")
    if isinstance(note_type, str) and note_type in TYPE_TO_DIR and rel_path:
        prefix = TYPE_TO_DIR[note_type] + "/"
        if not rel_path.startswith(prefix):
            errors.append(f"type {note_type!r} must live under {prefix}")
    return errors


def golden_schema_errors(case: object) -> list[str]:
    return validate_instance(case, load_json_schema(GOLDEN_SCHEMA_PATH))


def _is_fence_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _heading_level(line: str) -> int | None:
    if not line.startswith("#"):
        return None
    i = 0
    while i < len(line) and line[i] == "#":
        i += 1
    if 1 <= i <= 6 and i < len(line) and line[i] == " ":
        return i
    return None


def _split_sections(body: str, min_level: int, max_level: int) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(lines).strip()
        if text:
            sections.append((heading, text))

    for line in body.split("\n"):
        if _is_fence_line(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        level = None if in_fence else _heading_level(line)
        if level is not None and min_level <= level <= max_level:
            flush()
            heading = line[level + 1 :].strip()
            lines = []
        else:
            lines.append(line)
    flush()
    return sections


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _split_long(text: str, size: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    if len(text) <= size:
        return [text]
    step = max(size - overlap, 1)
    pieces: list[str] = []
    start = 0
    while start < len(text):
        pieces.append(text[start : start + size])
        if start + size >= len(text):
            break
        start += step
    return pieces


def chunk_markdown(raw: str, title: str = "") -> list[Chunk]:
    meta, body = parse_frontmatter(raw)
    tags_raw = meta.get("tags") or []
    tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
    if not title:
        title = str(meta.get("title") or "")
    if not title:
        title = note_title(raw, Path("note.md"))
    skip = {h.lower() for h in SKIP_HEADINGS}
    chunks: list[Chunk] = []

    def consider(heading: str, text: str) -> None:
        if heading.lower() in skip:
            return
        text = text.strip()
        if len(text) < MIN_CHUNK_CHARS:
            return
        for piece in _split_long(text):
            if not piece:
                continue
            if len(piece) < MIN_CHUNK_CHARS and len(text) <= MAX_CHUNK_CHARS:
                continue
            chunks.append(Chunk(heading=heading, text=piece, tags=tags))

    h2_sections = _split_sections(body, 2, 2)
    if not h2_sections:
        consider(title, body)
        return chunks

    for heading, text in h2_sections:
        if heading.lower() in skip:
            continue
        if len(text) > MAX_CHUNK_CHARS:
            h3 = _split_sections(text, 3, 3)
            if len(h3) > 1:
                for sub_h, sub_t in h3:
                    consider(sub_h or heading, sub_t)
                continue
        consider(heading, text)
    return chunks


def iter_markdown(vault: Path):
    vault = vault.resolve()
    for path in sorted(vault.rglob("*.md")):
        rel_parts = path.resolve().relative_to(vault).parts
        if any(part in SKIP_DIR_NAMES for part in rel_parts):
            continue
        yield path


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel_posix(vault: Path, path: Path) -> str:
    return path.resolve().relative_to(vault.resolve()).as_posix()


def note_title(raw: str, path: Path) -> str:
    meta, body = parse_frontmatter(raw)
    title = str(meta.get("title") or "").strip()
    if title:
        return title
    in_fence = False
    for line in body.splitlines():
        if _is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _heading_level(line) == 1:
            return line[2:].strip()
    return path.stem


def _fts_quote(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def fts_match_query(query: str) -> str:
    phrases = re.findall(r'"([^"]+)"', query)
    rest = re.sub(r'"[^"]*"', " ", query)
    terms = re.findall(r"\w+", rest, flags=re.UNICODE)
    parts: list[str] = [_fts_quote(p) for p in phrases if p.strip()]
    if len(terms) == 1 and not parts:
        return terms[0]
    if terms:
        joined = " OR ".join(terms)
        parts.append(f"({joined})" if phrases else joined)
    return " AND ".join(parts) if parts else '""'


def rrf_fuse(rank_lists: list[list], rrf_k: int = RRF_K) -> list[tuple]:
    scores: dict = {}
    for ranking in rank_lists:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], str(kv[0])))


def _assert_rel_safe(rel: str) -> None:
    raw = (rel or "").strip()
    if not raw:
        print(PATH_OUTSIDE, file=sys.stderr)
        raise SystemExit(1)
    if raw.startswith("\\\\") or raw.startswith("//"):
        print(PATH_OUTSIDE, file=sys.stderr)
        raise SystemExit(1)
    if re.match(r"^[a-zA-Z]:", raw) or Path(raw).is_absolute():
        print(PATH_OUTSIDE, file=sys.stderr)
        raise SystemExit(1)
    if ".." in Path(raw).parts:
        print(PATH_OUTSIDE, file=sys.stderr)
        raise SystemExit(1)


def resolve_in_vault(vault: Path, rel: str) -> Path:
    _assert_rel_safe(rel)
    vault_r = vault.resolve()
    dest = (vault_r / rel).resolve()
    try:
        dest.relative_to(vault_r)
    except ValueError:
        print(PATH_OUTSIDE, file=sys.stderr)
        raise SystemExit(1) from None
    return dest


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), factory=KbConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.vec_enabled = False
    conn.vec_degraded = False
    if sqlite_vec is not None and hasattr(conn, "enable_load_extension"):
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.vec_enabled = True
        except (AttributeError, sqlite3.Error):
            conn.vec_enabled = False
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _create_vec(conn: sqlite3.Connection) -> None:
    if not _vec_on(conn):
        return
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{EMBED_DIM}])"
    )


def _schema_sql() -> str:
    return """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
          path TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          mtime REAL NOT NULL,
          sha256 TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY,
          path TEXT NOT NULL,
          heading TEXT NOT NULL DEFAULT '',
          text TEXT NOT NULL,
          embedding BLOB,
          FOREIGN KEY(path) REFERENCES notes(path) ON DELETE CASCADE
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          text,
          heading,
          tokenize = 'porter unicode61'
        );
        """


def _drop_derived(conn: sqlite3.Connection) -> None:
    for sql in (
        "DROP TABLE IF EXISTS chunks_fts",
        "DROP TABLE IF EXISTS chunks_vec",
        "DROP TABLE IF EXISTS chunks",
        "DROP TABLE IF EXISTS notes",
    ):
        try:
            conn.execute(sql)
        except sqlite3.Error as exc:
            print(f"schema rebuild: {exc}", file=sys.stderr)


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    current = _meta_get(conn, "schema_version")
    notes_n = 0
    try:
        notes_n = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
    except sqlite3.Error:
        notes_n = 0
    if current != SCHEMA_VERSION:
        if current or notes_n:
            print(
                f"schema {current or 'unset'} -> {SCHEMA_VERSION}; rebuilding derived index",
                file=sys.stderr,
            )
            _drop_derived(conn)
        conn.executescript(_schema_sql())
        _create_vec(conn)
        _meta_set(conn, "schema_version", SCHEMA_VERSION)
        conn.commit()
        return
    conn.executescript(_schema_sql())
    _create_vec(conn)
    conn.commit()


def _wipe_chunks(conn: sqlite3.Connection) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM chunks")]
    for chunk_id in ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
        if _vec_on(conn):
            try:
                conn.execute("DELETE FROM chunks_vec WHERE rowid = ?", (chunk_id,))
            except sqlite3.Error as exc:
                conn.vec_degraded = True
                print(f"chunks_vec delete failed: {exc}", file=sys.stderr)
    conn.execute("DELETE FROM chunks")
    conn.execute("DELETE FROM notes")
    conn.commit()


def _ensure_embed_model(conn: sqlite3.Connection, embedder: object, force: bool = False) -> None:
    model_id = str(getattr(embedder, "model_id"))
    revision = str(getattr(embedder, "revision", "local"))
    current = _meta_get(conn, "embed_model")
    current_rev = _meta_get(conn, "embed_revision")
    stored_dim = _meta_get(conn, "embed_dim")
    stored_chunker = _meta_get(conn, "chunker_version")
    changed = bool(current) and current != model_id
    rev_changed = bool(current) and bool(current_rev) and current_rev != revision
    dim_changed = bool(stored_dim) and stored_dim != str(EMBED_DIM)
    if changed or rev_changed or dim_changed:
        if not force:
            print(
                f"index embedder changed ({current}@{current_rev or '?'} -> {model_id}@{revision}). "
                "Re-run with --force to rebuild the derived index.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"rebuilding index for embedder change {current} -> {model_id}", file=sys.stderr)
        _wipe_chunks(conn)
    elif stored_chunker and stored_chunker != CHUNKER_VERSION:
        print(
            f"chunker {stored_chunker} -> {CHUNKER_VERSION}; reindexing unchanged sources",
            file=sys.stderr,
        )
        _wipe_chunks(conn)
    _meta_set(conn, "embed_model", model_id)
    _meta_set(conn, "embed_revision", revision)
    _meta_set(conn, "embed_dim", str(EMBED_DIM))
    _meta_set(conn, "chunker_version", CHUNKER_VERSION)
    conn.commit()


def _delete_path(conn: sqlite3.Connection, rel: str) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE path = ?", (rel,))]
    for chunk_id in ids:
        conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
        if _vec_on(conn):
            try:
                conn.execute("DELETE FROM chunks_vec WHERE rowid = ?", (chunk_id,))
            except sqlite3.Error as exc:
                conn.vec_degraded = True
                print(f"chunks_vec delete failed: {exc}", file=sys.stderr)
    conn.execute("DELETE FROM chunks WHERE path = ?", (rel,))
    conn.execute("DELETE FROM notes WHERE path = ?", (rel,))


def _insert_chunk(
    conn: sqlite3.Connection,
    rel: str,
    heading: str,
    text: str,
    embedding: list[float] | None,
) -> None:
    blob = pack_vec(embedding) if embedding is not None else None
    cur = conn.execute(
        "INSERT INTO chunks(path, heading, text, embedding) VALUES (?, ?, ?, ?)",
        (rel, heading, text, blob),
    )
    chunk_id = cur.lastrowid
    conn.execute(
        "INSERT INTO chunks_fts(rowid, text, heading) VALUES (?, ?, ?)",
        (chunk_id, text, heading),
    )
    if embedding is not None and _vec_on(conn):
        try:
            conn.execute(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                (chunk_id, serialize_query(embedding)),
            )
        except sqlite3.Error as exc:
            conn.vec_degraded = True
            print(f"chunks_vec insert failed: {exc}", file=sys.stderr)


def index_file(
    conn: sqlite3.Connection,
    vault: Path,
    path: Path,
    embedder: object | None,
    force: bool = False,
    schema_issues: list[str] | None = None,
) -> str:
    if embedder is not None:
        _ensure_embed_model(conn, embedder, force=force)
    rel = rel_posix(vault, path)
    digest = file_sha256(path)
    raw = path.read_text(encoding="utf-8")
    meta, _body = parse_frontmatter(raw)
    if schema_issues is not None:
        for err in note_schema_errors(meta, rel):
            schema_issues.append(f"{rel}: {err}")
    row = conn.execute("SELECT sha256 FROM notes WHERE path = ?", (rel,)).fetchone()
    if row is not None and str(row[0]) == digest:
        return "skipped"
    title = note_title(raw, path)
    chunks = chunk_markdown(raw, title=title)
    tags = meta.get("tags") or []
    tag_s = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
    _delete_path(conn, rel)
    conn.execute(
        "INSERT INTO notes(path, title, mtime, sha256, tags) VALUES (?, ?, ?, ?, ?)",
        (rel, title, path.stat().st_mtime, digest, tag_s),
    )
    texts = []
    for chunk in chunks:
        prefix = " | ".join(p for p in [title, chunk.heading, tag_s] if p)
        texts.append(f"{prefix}\n{chunk.text}" if prefix else chunk.text)
    embeddings: list[list[float] | None]
    if embedder is not None and texts:
        embeddings = list(embedder.encode(texts))  # type: ignore[union-attr]
        while len(embeddings) < len(texts):
            embeddings.append(None)
    else:
        embeddings = [None] * len(texts)
    for chunk, text, emb in zip(chunks, texts, embeddings, strict=False):
        _insert_chunk(conn, rel, chunk.heading, text, emb)
    conn.commit()
    return "updated"


def index_vault(
    conn: sqlite3.Connection,
    vault: Path,
    embedder: object | None,
    force: bool = False,
) -> dict:
    if embedder is not None:
        _ensure_embed_model(conn, embedder, force=force)
    seen: set[str] = set()
    updated = 0
    skipped = 0
    errors = 0
    error_paths: list[str] = []
    schema_issues: list[str] = []
    for path in iter_markdown(vault):
        rel = rel_posix(vault, path)
        seen.add(rel)
        try:
            action = index_file(
                conn, vault, path, embedder, force=force, schema_issues=schema_issues
            )
        except (OSError, UnicodeDecodeError) as exc:
            errors += 1
            error_paths.append(f"{rel}: {exc}")
            continue
        if action == "skipped":
            skipped += 1
        else:
            updated += 1
    stale = [
        str(row[0])
        for row in conn.execute("SELECT path FROM notes")
        if str(row[0]) not in seen
    ]
    for rel in stale:
        _delete_path(conn, rel)
    _meta_set(conn, "indexed_at", datetime.now(timezone.utc).isoformat())
    _meta_set(conn, "schema_invalid", str(len(schema_issues)))
    conn.commit()
    notes = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
    chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    degraded = bool(errors or getattr(conn, "vec_degraded", False) or embedder is None)
    return {
        "notes": notes,
        "chunks": chunks,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "error_paths": error_paths,
        "schema_invalid": len(schema_issues),
        "schema_invalid_paths": schema_issues,
        "degraded": degraded,
    }


def _lexical_ids(conn: sqlite3.Connection, query: str, limit: int) -> list[int]:
    match = fts_match_query(query)
    try:
        rows = conn.execute(
            """
            SELECT chunks.id
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts)
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
    except sqlite3.Error:
        rows = conn.execute(
            """
            SELECT chunks.id
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
    return [int(r[0]) for r in rows]


def _semantic_ids(
    conn: sqlite3.Connection,
    query: str,
    embedder: object | None,
    limit: int,
) -> list[int]:
    if embedder is None:
        return []
    qvec = embedder.encode([query])[0]  # type: ignore[union-attr]
    if _vec_on(conn):
        _backfill_chunks_vec(conn)
        payload = serialize_query(qvec)
        try:
            rows = conn.execute(
                """
                SELECT rowid, distance
                FROM chunks_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (payload, limit),
            ).fetchall()
            ids = [int(r[0]) for r in rows]
            if ids:
                return ids
        except sqlite3.Error:
            try:
                rows = conn.execute(
                    """
                    SELECT rowid, distance
                    FROM chunks_vec
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                    """,
                    (payload, limit),
                ).fetchall()
                ids = [int(r[0]) for r in rows]
                if ids:
                    return ids
            except sqlite3.Error as exc:
                conn.vec_degraded = True
                print(f"chunks_vec query failed; cosine fallback: {exc}", file=sys.stderr)
        blobs = int(
            conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
        )
        if blobs:
            conn.vec_degraded = True
            print("chunks_vec empty with stored embeddings; cosine fallback", file=sys.stderr)
    scored: list[tuple[float, int]] = []
    for row in conn.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"):
        blob = row[1]
        if not blob:
            continue
        scored.append((cosine(qvec, unpack_vec(blob)), int(row[0])))
    scored.sort(key=lambda item: -item[0])
    return [chunk_id for _score, chunk_id in scored[:limit]]


def _note_type_prefix(note_type: str | None) -> str | None:
    if not note_type:
        return None
    folder = TYPE_TO_DIR.get(note_type)
    return None if folder is None else f"{folder}/"


def _parse_since(since: str | None) -> float | None:
    if not since:
        return None
    raw = since.strip()
    try:
        if len(raw) <= 10:
            return datetime.fromisoformat(raw).timestamp()
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        print(f"invalid --since value: {since}", file=sys.stderr)
        raise SystemExit(1) from None


def search(
    conn: sqlite3.Connection,
    query: str,
    embedder: object | None,
    mode: str = "hybrid",
    k: int = 8,
    warnings: list[str] | None = None,
    tag: str | None = None,
    note_type: str | None = None,
    since: str | None = None,
    max_per_note: int = MAX_PER_NOTE,
    full: bool = False,
) -> list[dict]:
    pool = max(k * 8, 50)
    lexical: list[int] = []
    semantic: list[int] = []
    if mode in {"hybrid", "lexical"}:
        lexical = _lexical_ids(conn, query, pool)
    if mode in {"hybrid", "semantic"}:
        semantic = _semantic_ids(conn, query, embedder, pool)
        if embedder is None and warnings is not None:
            warnings.append("no embedder; semantic search skipped")
        if mode == "semantic" and not semantic and lexical:
            if warnings is not None:
                warnings.append("semantic empty; nothing to rank")
    if mode == "lexical":
        ordered = [(i, 1.0 / (RRF_K + rank + 1)) for rank, i in enumerate(lexical)]
    elif mode == "semantic":
        ordered = [(i, 1.0 / (RRF_K + rank + 1)) for rank, i in enumerate(semantic)]
    else:
        lists = [lst for lst in (lexical, semantic) if lst]
        if not lists:
            ordered = []
        else:
            ordered = rrf_fuse(lists, rrf_k=RRF_K)
    lexical_set = set(lexical)
    semantic_set = set(semantic)
    type_prefix = _note_type_prefix(note_type)
    since_ts = _parse_since(since)
    tag_l = tag.strip().lower() if tag else None
    per_note: dict[str, int] = {}
    results: list[dict] = []
    for chunk_id, score in ordered:
        if len(results) >= k:
            break
        row = conn.execute(
            """
            SELECT chunks.path, chunks.heading, chunks.text, notes.tags, notes.mtime
            FROM chunks
            JOIN notes ON notes.path = chunks.path
            WHERE chunks.id = ?
            """,
            (chunk_id,),
        ).fetchone()
        if row is None:
            continue
        path = str(row[0])
        if type_prefix and not path.startswith(type_prefix):
            continue
        if since_ts is not None and float(row[4]) < since_ts:
            continue
        if tag_l:
            tags = [t.strip().lower() for t in str(row[3] or "").split(",") if t.strip()]
            if tag_l not in tags:
                continue
        if per_note.get(path, 0) >= max_per_note:
            continue
        per_note[path] = per_note.get(path, 0) + 1
        sources = []
        if chunk_id in lexical_set:
            sources.append("fts")
        if chunk_id in semantic_set:
            sources.append("vector")
        source = "+".join(sources) if sources else mode
        if source == "fts+vector":
            source = "vector+fts"
        text = str(row[2])
        if not full and len(text) > SNIPPET_CHARS:
            text = text[:SNIPPET_CHARS].rstrip() + "…"
        results.append(
            {
                "path": path,
                "heading": str(row[1]),
                "score": float(score),
                "text": text,
                "source": source,
            }
        )
    if warnings is not None and getattr(conn, "vec_degraded", False):
        msg = "chunks_vec query failed; cosine fallback"
        if msg not in warnings:
            warnings.append(msg)
    return results


def index_freshness(conn: sqlite3.Connection, vault: Path | None) -> tuple[str | None, int]:
    indexed_at = _meta_get(conn, "indexed_at")
    stale = 0
    if vault is None or not Path(vault).is_dir():
        return indexed_at, stale
    cutoff = 0.0
    if indexed_at:
        try:
            cutoff = datetime.fromisoformat(indexed_at).timestamp()
        except ValueError:
            cutoff = 0.0
    for path in iter_markdown(Path(vault)):
        try:
            if path.stat().st_mtime > cutoff + 1e-6:
                stale += 1
        except OSError:
            stale += 1
    return indexed_at, stale


def _db_filename(env: dict[str, str] | os._Environ) -> str:
    kind = (env.get("FDE_KB_EMBEDDER") or "").strip().lower()
    if kind in ("fake", "hash"):
        return "index-hash-256.sqlite"
    return "index.sqlite"


def resolve_db(env: dict[str, str] | os._Environ | None = None) -> Path:
    env = env or os.environ
    raw = (env.get("FDE_KB_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    name = _db_filename(env)
    return cache_root(env) / name


def _parse_vaults_verbose(stdout: str, name: str) -> Path | None:
    current_name = ""
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "\t" in stripped and not stripped.lower().startswith("name"):
            vname, raw_path = stripped.split("\t", 1)
            path = Path(raw_path.strip())
            if vname.strip() == name or path.name == name:
                if path.is_dir():
                    return path
            continue
        lower = stripped.lower()
        if lower.startswith("name"):
            current_name = stripped.split(None, 1)[-1] if " " in stripped else stripped.split("=", 1)[-1]
            current_name = current_name.strip()
        if lower.startswith("path"):
            raw_path = stripped.split(None, 1)[-1] if " " in stripped else stripped.split("=", 1)[-1]
            raw_path = raw_path.strip()
            if current_name == name or Path(raw_path).name == name:
                path = Path(raw_path)
                if path.is_dir():
                    return path
    return None


def resolve_vault(
    env: dict[str, str] | os._Environ | None = None,
    run_obsidian=None,
) -> Path:
    env = env or os.environ
    raw = (env.get("FDE_KB_VAULT") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_dir():
            return path.resolve()
        print(f"FDE_KB_VAULT is not a directory: {raw}", file=sys.stderr)
        raise SystemExit(1)
    name = (env.get("FDE_KB_VAULT_NAME") or "").strip()
    runner = run_obsidian
    if name and runner is not None:
        try:
            proc = runner(["obsidian", "vaults", "verbose"])
            stdout = getattr(proc, "stdout", "") or ""
            found = _parse_vaults_verbose(stdout, name)
            if found is not None:
                return found.resolve()
            proc2 = runner(["obsidian", f"vault={name}", "vault", "info=path"])
            candidate = Path((getattr(proc2, "stdout", "") or "").strip())
            if candidate.is_dir():
                return candidate.resolve()
        except FileNotFoundError:
            pass
    print(MISSING_VAULT_HINT, file=sys.stderr)
    print(SANDBOX_HINT, file=sys.stderr)
    raise SystemExit(1)


def vault_name_for(vault: Path, env: dict[str, str] | os._Environ | None = None) -> str:
    env = env or os.environ
    return (env.get("FDE_KB_VAULT_NAME") or "").strip() or vault.name


def _windows_obsidian_candidates(env: dict[str, str] | os._Environ) -> list[Path]:
    local = Path(env.get("LOCALAPPDATA") or "")
    pf = Path(env.get("ProgramFiles") or r"C:\Program Files")
    pf86 = Path(env.get("ProgramFiles(x86)") or r"C:\Program Files (x86)")
    return [
        local / "Programs" / "obsidian" / "Obsidian.exe",
        local / "Programs" / "Obsidian" / "Obsidian.exe",
        local / "Obsidian" / "Obsidian.exe",
        pf / "Obsidian" / "Obsidian.exe",
        pf86 / "Obsidian" / "Obsidian.exe",
    ]


def obsidian_exe() -> str | None:
    env_path = (os.environ.get("FDE_KB_OBSIDIAN") or "").strip()
    if env_path and Path(env_path).is_file():
        return env_path
    found = shutil.which("obsidian")
    if found:
        return found
    if sys.platform == "win32":
        for cand in _windows_obsidian_candidates(os.environ):
            if cand.is_file():
                return str(cand)
        return None
    mac = Path("/Applications/Obsidian.app/Contents/MacOS/obsidian")
    if mac.is_file():
        return str(mac)
    local = Path.home() / "Applications" / "Obsidian.app" / "Contents" / "MacOS" / "obsidian"
    if local.is_file():
        return str(local)
    return None


def obsidian_cli_ok() -> bool:
    if obsidian_exe() is None:
        return False
    try:
        proc = default_run_obsidian(["obsidian", "help"], timeout=OBSIDIAN_PROBE_TIMEOUT)
    except FileNotFoundError:
        return False
    text = f"{getattr(proc, 'stdout', '') or ''}{getattr(proc, 'stderr', '') or ''}"
    if CLI_DISABLED_MSG.lower() in text.lower():
        return False
    if sys.platform == "win32" and not text.strip():
        # GUI-subsystem Obsidian.exe often detaches with empty stdout.
        return False
    return getattr(proc, "returncode", 1) in (0, None) and "Usage:" in text


def _decode_proc_bytes(data) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def default_run_obsidian(cmd: list[str], timeout: float | int | None = None, **_kwargs):
    exe = obsidian_exe()
    if not exe:
        raise FileNotFoundError("obsidian")
    argv = [exe, *cmd[1:]] if cmd and cmd[0] == "obsidian" else cmd
    if timeout is None:
        timeout = int(os.environ.get("FDE_KB_OBSIDIAN_TIMEOUT", "20"))
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            argv,
            returncode=124,
            stdout=_decode_proc_bytes(exc.stdout),
            stderr=_decode_proc_bytes(exc.stderr)
            + "obsidian CLI timed out. Enable Command line interface in Settings → General.",
        )


CLI_DISABLED_MSG = "Command line interface is not enabled"
VAULT_NOT_FOUND_MSG = "vault not found"


def _obsidian_error(proc) -> str | None:
    text = f"{getattr(proc, 'stderr', '') or ''}{getattr(proc, 'stdout', '') or ''}"
    lower = text.lower()
    if CLI_DISABLED_MSG.lower() in lower:
        return MISSING_OBSIDIAN_HINT
    if VAULT_NOT_FOUND_MSG in lower:
        return text.strip() or "Vault not found"
    code = getattr(proc, "returncode", 0)
    if code not in (0, None):
        return text.strip() or f"obsidian exited {code}"
    return None


def _run_obsidian(cmd: list[str], run_obsidian=None, timeout: float | int | None = None):
    runner = run_obsidian or default_run_obsidian
    try:
        proc = runner(cmd, timeout=timeout) if timeout is not None else runner(cmd)
    except FileNotFoundError:
        print(MISSING_OBSIDIAN_HINT, file=sys.stderr)
        raise SystemExit(1) from None
    return proc


def _try_obsidian(cmd: list[str], run_obsidian, warnings: list[str]) -> bool:
    try:
        proc = _run_obsidian(cmd, run_obsidian=run_obsidian)
    except SystemExit:
        warnings.append(MISSING_OBSIDIAN_HINT)
        warnings.append("fell back to writing the vault file on disk")
        return False
    err = _obsidian_error(proc)
    if err is None:
        return True
    warnings.append(err)
    warnings.append("fell back to writing the vault file on disk")
    return False


def get_note(
    rel_path: str,
    vault_name: str,
    run_obsidian=None,
    vault: Path | None = None,
) -> str:
    dest: Path | None = None
    if vault is not None:
        dest = resolve_in_vault(vault, rel_path)
        rel_path = dest.relative_to(vault.resolve()).as_posix()
    else:
        _assert_rel_safe(rel_path)
    proc = _run_obsidian(
        ["obsidian", f"vault={vault_name}", "read", f"path={rel_path}"],
        run_obsidian=run_obsidian,
        timeout=OBSIDIAN_PROBE_TIMEOUT,
    )
    err = _obsidian_error(proc)
    if err is None:
        return getattr(proc, "stdout", "") or ""
    if dest is not None and dest.is_file():
        return dest.read_text(encoding="utf-8")
    print(err, file=sys.stderr)
    raise SystemExit(1)


def normalize_tags(note_type: str, tags: object = None) -> list[str]:
    """Type tag first, then caller tags, deduped, order preserved."""
    if isinstance(tags, str):
        raw = tags.split(",")
    elif isinstance(tags, (list, tuple)):
        raw = [str(t) for t in tags]
    else:
        raw = []
    out: list[str] = []
    for candidate in [note_type, *raw]:
        tag = str(candidate).strip().strip("'").strip('"')
        # A tag containing a comma or bracket would break the inline YAML array.
        tag = tag.replace(",", " ").replace("[", "").replace("]", "").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def _template_body(note_type: str, title: str, body: str, tags: object = None) -> str:
    path = SKILL_ROOT / "assets" / "templates" / f"{note_type}.md"
    raw = path.read_text(encoding="utf-8")
    rendered_tags = ", ".join(normalize_tags(note_type, tags))
    return (
        raw.replace("{title}", title)
        .replace("{tags}", rendered_tags)
        .replace("{body}", body)
    )


def _disk_write(dest: Path, content: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def _disk_append(dest: Path, body: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    chunk = body if body.endswith("\n") else body + "\n"
    if dest.is_file():
        existing = dest.read_text(encoding="utf-8")
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        dest.write_text(existing + prefix + chunk, encoding="utf-8")
        return
    dest.write_text(chunk, encoding="utf-8")


def ingest(
    vault: Path,
    db_path: Path,
    note_type: str,
    title: str,
    body: str = "",
    embedder: object | None = None,
    run_obsidian=None,
    vault_name: str | None = None,
    warnings: list[str] | None = None,
    tags: object = None,
) -> str:
    if note_type not in TYPE_TO_DIR:
        print(f"unknown type: {note_type}", file=sys.stderr)
        raise SystemExit(1)
    warn = warnings if warnings is not None else []
    folder = TYPE_TO_DIR[note_type]
    slug = slugify(title)
    rel = f"{folder}/{slug}.md"
    dest = resolve_in_vault(vault, rel)
    n = 2
    while dest.is_file():
        rel = f"{folder}/{slug}-{n}.md"
        dest = resolve_in_vault(vault, rel)
        n += 1
    content = _template_body(note_type, title, body, tags)
    name = vault_name or vault.name
    cli_ok = _try_obsidian(
        ["obsidian", f"vault={name}", "create", f"path={rel}", f"content={content}"],
        run_obsidian,
        warn,
    )
    if not cli_ok:
        _disk_write(dest, content)
    elif not dest.is_file():
        warn.append("obsidian create reported success but the vault file is missing; wrote disk copy")
        _disk_write(dest, content)
    conn = connect(db_path)
    init_schema(conn)
    index_file(conn, vault, dest, embedder)
    conn.close()
    return rel


def _leading_h1(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped[2:].strip() if stripped.startswith("# ") else ""
    return ""


def _strip_leading_h1(body: str) -> str:
    """The template re-adds an H1, so drop the source's own to avoid two."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            return "\n".join(lines[i + 1 :]).lstrip("\n")
        return body
    return body


def _read_text_file(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        print(
            f"fde-kb: {label} is not UTF-8: {path}. Re-save it as UTF-8 and retry.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


def read_body_source(body: str, body_file: str) -> str:
    """Long documents do not fit in argv; cmd.exe caps a command line at 8191 chars."""
    if not body_file:
        return body
    if body_file == "-":
        return sys.stdin.read()
    path = Path(body_file).expanduser()
    if not path.is_file():
        print(f"fde-kb: --body-file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    return _read_text_file(path, "--body-file")


def import_note(
    vault: Path,
    db_path: Path,
    source: str,
    note_type: str,
    title: str = "",
    tags: object = None,
    embedder: object | None = None,
    run_obsidian=None,
    vault_name: str | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Adopt an existing document into the vault with schema-valid frontmatter."""
    path = Path(source).expanduser()
    if not path.is_file():
        print(f"fde-kb: file not found: {path}", file=sys.stderr)
        raise SystemExit(1)
    meta, body = parse_frontmatter(_read_text_file(path, "source file"))
    resolved_title = (
        (title or "").strip()
        or str(meta.get("title") or "").strip()
        or _leading_h1(body)
        or path.stem.replace("-", " ").replace("_", " ").strip()
        or "untitled"
    )
    if tags is None and isinstance(meta.get("tags"), list):
        tags = [str(t) for t in meta["tags"]]
    return ingest(
        vault=vault,
        db_path=db_path,
        note_type=note_type,
        title=resolved_title,
        body=_strip_leading_h1(body).strip(),
        embedder=embedder,
        run_obsidian=run_obsidian,
        vault_name=vault_name,
        warnings=warnings,
        tags=tags,
    )


def append_note(
    vault: Path,
    db_path: Path,
    rel_path: str,
    body: str,
    embedder: object | None = None,
    run_obsidian=None,
    vault_name: str | None = None,
    warnings: list[str] | None = None,
) -> None:
    warn = warnings if warnings is not None else []
    dest = resolve_in_vault(vault, rel_path)
    rel_path = dest.relative_to(vault.resolve()).as_posix()
    name = vault_name or vault.name
    cli_ok = _try_obsidian(
        ["obsidian", f"vault={name}", "append", f"path={rel_path}", f"content={body}"],
        run_obsidian,
        warn,
    )
    if not cli_ok:
        _disk_append(dest, body)
    conn = connect(db_path)
    init_schema(conn)
    if dest.is_file():
        index_file(conn, vault, dest, embedder)
    conn.close()


def _vec_row_count(conn: sqlite3.Connection) -> int:
    if not _vec_on(conn):
        return 0
    try:
        return int(conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0])
    except sqlite3.Error:
        return 0


def _backfill_chunks_vec(conn: sqlite3.Connection) -> bool:
    """Copy stored embedding blobs into chunks_vec. Returns True if the table lagged."""
    if not _vec_on(conn):
        return False
    try:
        blobs = int(
            conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
        )
        vecs = _vec_row_count(conn)
    except sqlite3.Error:
        return False
    if blobs <= vecs:
        return False
    try:
        existing = {int(row[0]) for row in conn.execute("SELECT rowid FROM chunks_vec")}
    except sqlite3.Error as exc:
        conn.vec_degraded = True
        print(f"chunks_vec backfill failed: {exc}", file=sys.stderr)
        return True
    for row in conn.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"):
        chunk_id = int(row[0])
        blob = row[1]
        if not blob or chunk_id in existing:
            continue
        try:
            conn.execute(
                "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
                (chunk_id, serialize_query(unpack_vec(blob))),
            )
        except sqlite3.Error as exc:
            conn.vec_degraded = True
            print(f"chunks_vec backfill failed: {exc}", file=sys.stderr)
            break
    conn.commit()
    return True


def status_payload(
    vault: Path | None,
    db_path: Path,
    obsidian_ok: bool | None = None,
) -> dict:
    if obsidian_ok is None:
        obsidian_ok = obsidian_exe() is not None
    conn = connect(db_path)
    init_schema(conn)
    notes = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
    chunks = int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    embedding_blobs = int(
        conn.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
    )
    vectors = _vec_row_count(conn)
    model = _meta_get(conn, "embed_model") or ""
    revision = _meta_get(conn, "embed_revision") or ""
    schema = _meta_get(conn, "schema_version") or ""
    chunker = _meta_get(conn, "chunker_version") or ""
    schema_invalid_raw = _meta_get(conn, "schema_invalid") or "0"
    indexed_at, stale = index_freshness(conn, vault)
    vec_ok = _vec_on(conn)
    vec_degraded = bool(getattr(conn, "vec_degraded", False))
    conn.close()
    warnings: list[str] = []
    vault_s = str(vault) if vault is not None else None
    if vault is None or not Path(vault).is_dir():
        warnings.append(MISSING_VAULT_HINT)
        warnings.append(SANDBOX_HINT)
    if not obsidian_ok:
        warnings.append(MISSING_OBSIDIAN_HINT)
        if sys.platform == "win32":
            warnings.append(
                "On Windows, Obsidian.exe is a GUI app and often returns empty CLI output. "
                "Writes then use vault files on disk."
            )
    if not vec_ok:
        warnings.append(
            "sqlite-vec not loaded; using FTS5 and in-process cosine over stored embeddings."
        )
    if vec_degraded:
        warnings.append("chunks_vec writes or queries failed; vector index is degraded.")
    vec_unpopulated = bool(vec_ok and embedding_blobs > vectors)
    if vec_unpopulated:
        warnings.append(
            f"vector index is unpopulated ({vectors} vec rows, {embedding_blobs} stored embeddings)"
        )
    try:
        schema_invalid = int(schema_invalid_raw)
    except ValueError:
        schema_invalid = 0
    if schema_invalid:
        warnings.append(
            f"{schema_invalid} note(s) do not match assets/schemas/note.schema.json"
        )
    model_path = find_model_dir()
    model_ready = model_path is not None
    if not model_ready:
        warnings.append(missing_model_message(_looked_model_path()))
    uv_index = resolved_uv_index()
    if uv_index is None and not allow_public_index():
        warnings.append(UV_INDEX_HINT)
    degraded = (not vec_ok) or vec_degraded or vec_unpopulated
    return {
        "vault": vault_s,
        "vault_name": vault.name if vault is not None else None,
        "db": str(db_path),
        "notes": notes,
        "chunks": chunks,
        "vectors": vectors,
        "embedding_blobs": embedding_blobs,
        "embed_model": model,
        "embed_revision": revision,
        "embed_dim": EMBED_DIM,
        "schema_version": schema,
        "schema_invalid": schema_invalid,
        "chunker_version": chunker,
        "indexed_at": indexed_at,
        "stale": stale,
        "sqlite_vec": vec_ok,
        "obsidian_cli": bool(obsidian_ok),
        "degraded": degraded,
        "model_path": str(model_path) if model_path is not None else None,
        "model_revision": EMBED_REVISION,
        "model_ready": model_ready,
        "uv_index": uv_index,
        "warnings": warnings,
    }


def load_golden(path: Path | str) -> list[dict]:
    target = Path(path)
    if not target.is_file():
        print(f"golden set not found: {target}", file=sys.stderr)
        raise SystemExit(1)
    cases: list[dict] = []
    for i, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            case = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"golden set line {i} is not JSON: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        errs = golden_schema_errors(case)
        if errs:
            print(f"golden set line {i} is invalid: {'; '.join(errs)}", file=sys.stderr)
            raise SystemExit(1)
        if not isinstance(case, dict):
            print(f"golden set line {i} is not an object", file=sys.stderr)
            raise SystemExit(1)
        cases.append(case)
    if not cases:
        print(f"golden set is empty: {target}", file=sys.stderr)
        raise SystemExit(1)
    return cases


def resolve_golden(explicit: str, vault: Path) -> Path:
    raw = (explicit or os.environ.get("FDE_KB_GOLDEN") or "").strip()
    target = Path(raw).expanduser() if raw else vault / "evals" / "golden.jsonl"
    if not target.is_file():
        print(MISSING_GOLDEN_HINT, file=sys.stderr)
        print(f"looked at: {target}", file=sys.stderr)
        raise SystemExit(1)
    return target


def eval_retrieval(
    conn: sqlite3.Connection,
    embedder: object | None,
    k: int = 8,
    cases: list[dict] | None = None,
    golden: Path | str | None = None,
) -> dict:
    if cases is not None:
        gold = cases
    elif golden is not None:
        gold = load_golden(golden)
    else:
        print("eval needs --golden PATH to a JSONL of query/path pairs", file=sys.stderr)
        raise SystemExit(1)
    report: dict[str, object] = {"k": k, "n": len(gold), "modes": {}}
    for mode in ("lexical", "semantic", "hybrid"):
        hits = 0
        mrr = 0.0
        for case in gold:
            results = search(
                conn,
                str(case["query"]),
                embedder,
                mode=mode,
                k=k,
                max_per_note=8,
                full=True,
            )
            paths = [r["path"] for r in results]
            expected = str(case["path"])
            if expected in paths:
                hits += 1
                mrr += 1.0 / (paths.index(expected) + 1)
        n = len(gold) or 1
        report["modes"][mode] = {  # type: ignore[index]
            "recall_at_k": hits / n,
            "mrr": mrr / n,
            "hits": hits,
        }
    return report


def get_embedder(warnings: list[str] | None = None) -> object | None:
    kind = (os.environ.get("FDE_KB_EMBEDDER") or "").strip().lower()
    if kind in ("fake", "hash"):
        return HashEmbedder()
    if kind in ("none", "off"):
        if warnings is not None:
            warnings.append("embedder disabled; lexical only")
        return None
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if find_model_dir() is None:
        msg = missing_model_message(_looked_model_path())
        print(msg, file=sys.stderr)
        if warnings is not None:
            warnings.append(msg)
        return None
    return Model2VecEmbedder()


def _main(argv: list[str]) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="fde-kb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_index = sub.add_parser("index")
    p_index.add_argument("--force", action="store_true")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--mode", choices=["hybrid", "lexical", "semantic"], default="hybrid")
    p_search.add_argument("-k", "--k", type=int, default=8)
    p_search.add_argument("--tag")
    p_search.add_argument("--type", dest="note_type", choices=["playbook", "engagement", "eval"])
    p_search.add_argument("--since")
    p_search.add_argument("--full", action="store_true")
    p_get = sub.add_parser("get")
    p_get.add_argument("path")
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--type", required=True, choices=["playbook", "engagement", "eval"])
    p_ing.add_argument("--title", required=True)
    p_ing.add_argument("--body", default="")
    p_ing.add_argument("--body-file", dest="body_file", default="", help="Read body from a file; '-' is stdin.")
    p_ing.add_argument("--tags", default="", help="Comma-separated. The type is always added.")
    p_imp = sub.add_parser("import", help="Adopt an existing document into the vault.")
    p_imp.add_argument("source")
    p_imp.add_argument("--type", required=True, choices=["playbook", "engagement", "eval"])
    p_imp.add_argument("--title", default="", help="Default: frontmatter title, else H1, else filename.")
    p_imp.add_argument("--tags", default="")
    p_app = sub.add_parser("append")
    p_app.add_argument("path")
    p_app.add_argument("--body", default="")
    p_app.add_argument("--body-file", dest="body_file", default="", help="Read body from a file; '-' is stdin.")
    p_eval = sub.add_parser("eval")
    p_eval.add_argument("-k", "--k", type=int, default=8)
    p_eval.add_argument(
        "--golden",
        default="",
        help="JSONL of {\"query\", \"path\"} pairs. Default: FDE_KB_GOLDEN or <vault>/evals/golden.jsonl.",
    )
    p_eval.add_argument(
        "--vault",
        default="",
        help="Vault to index for this eval. Default: FDE_KB_VAULT.",
    )
    args = parser.parse_args(argv)

    warnings: list[str] = []

    if args.cmd == "eval":
        if args.vault:
            eval_vault = Path(args.vault).expanduser()
            if not eval_vault.is_dir():
                print(f"eval vault is not a directory: {eval_vault}", file=sys.stderr)
                return 1
        else:
            eval_vault = resolve_vault(run_obsidian=default_run_obsidian)
        try:
            golden = resolve_golden(args.golden, eval_vault)
        except SystemExit:
            return 1
        embedder = get_embedder(warnings)
        live_db = resolve_db()
        db_path = live_db.with_name("eval-" + live_db.name)
        conn = connect(db_path)
        init_schema(conn)
        stats = index_vault(conn, eval_vault, embedder)
        if stats.get("errors"):
            warnings.append(f"index errors: {stats.get('error_paths')}")
        report = eval_retrieval(conn, embedder, k=args.k, golden=golden)
        report["vault"] = str(eval_vault)
        report["golden"] = str(golden)
        report["warnings"] = warnings
        report["degraded"] = bool(warnings)
        conn.close()
        print(json.dumps(report, indent=2))
        return 0

    if args.cmd == "status":
        db_path = resolve_db()
        vault: Path | None
        try:
            vault = resolve_vault(run_obsidian=default_run_obsidian)
        except SystemExit:
            payload = status_payload(None, db_path, obsidian_ok=obsidian_cli_ok())
            print(json.dumps(payload, indent=2))
            return 1
        print(json.dumps(status_payload(vault, db_path, obsidian_ok=obsidian_cli_ok()), indent=2))
        return 0

    vault = resolve_vault(run_obsidian=default_run_obsidian)
    db_path = resolve_db()
    name = vault_name_for(vault)

    if args.cmd == "get":
        sys.stdout.write(get_note(args.path, vault_name=name, vault=vault))
        return 0

    embedder = get_embedder(warnings)

    if args.cmd == "index":
        conn = connect(db_path)
        init_schema(conn)
        stats = index_vault(conn, vault, embedder, force=args.force)
        conn.close()
        print(json.dumps(stats))
        return 0 if not stats.get("errors") else 1

    if args.cmd == "search":
        conn = connect(db_path)
        init_schema(conn)
        if int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]) == 0:
            warnings.append("index was empty; ran a full index before search")
            index_vault(conn, vault, embedder)
        results = search(
            conn,
            args.query,
            embedder,
            mode=args.mode,
            k=args.k,
            warnings=warnings,
            tag=args.tag,
            note_type=args.note_type,
            since=args.since,
            full=args.full,
        )
        indexed_at, stale = index_freshness(conn, vault)
        vec_degraded = bool(getattr(conn, "vec_degraded", False))
        conn.close()
        ran_mode = args.mode
        if ran_mode == "hybrid" and (embedder is None or vec_degraded):
            ran_mode = "lexical"
        print(
            json.dumps(
                {
                    "query": args.query,
                    "mode": ran_mode,
                    "indexed_at": indexed_at,
                    "stale": stale,
                    "degraded": vec_degraded or bool(warnings),
                    "warnings": warnings,
                    "results": results,
                }
            )
        )
        return 0

    if args.cmd == "ingest":
        rel = ingest(
            vault=vault,
            db_path=db_path,
            note_type=args.type,
            title=args.title,
            body=read_body_source(args.body, args.body_file),
            embedder=embedder,
            vault_name=name,
            warnings=warnings,
            tags=args.tags,
        )
        print(json.dumps({"path": rel, "created": True, "warnings": warnings}))
        return 0

    if args.cmd == "import":
        rel = import_note(
            vault=vault,
            db_path=db_path,
            source=args.source,
            note_type=args.type,
            title=args.title,
            tags=args.tags or None,
            embedder=embedder,
            vault_name=name,
            warnings=warnings,
        )
        print(json.dumps({"path": rel, "created": True, "warnings": warnings}))
        return 0

    if args.cmd == "append":
        body = read_body_source(args.body, args.body_file)
        if not body:
            print("fde-kb: append needs --body or --body-file.", file=sys.stderr)
            return 1
        append_note(
            vault=vault,
            db_path=db_path,
            rel_path=args.path,
            body=body,
            embedder=embedder,
            vault_name=name,
            warnings=warnings,
        )
        print(json.dumps({"path": args.path, "warnings": warnings}))
        return 0

    parser.error("unknown command")
    return 2


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
