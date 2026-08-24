"""Write a plain-English HTML dashboard. Three skills only. Self-contained."""

from __future__ import annotations

import html
import json
from pathlib import Path

DISPLAY_SKILLS = (
    ("graph_memory", "graph-memory", "graph"),
    ("fde_kb", "fde-kb", "kb"),
    ("llm_wiki", "llm-wiki", "wiki"),
)

HOW_IT_WORKS = {
    "graph_memory": {
        "one_line": "Finds linked facts (who owns what, who approves what).",
        "steps": [
            "Scan the repo once and build a small fact graph.",
            "On each question, walk a few links and return a short fact list.",
        ],
        "best_for": "Who / what / who approves questions.",
    },
    "fde_kb": {
        "one_line": "Search the repo like Google for your docs.",
        "steps": [
            "Index the files once.",
            "On each question, return the best matching text chunks.",
        ],
        "best_for": "How-to wording, URLs, tone, anything buried in prose.",
    },
    "llm_wiki": {
        "one_line": "Harness agent compiles wiki pages; CLI search runs on those pages.",
        "steps": [
            "Poolside / Codex / Claude reads sources and writes wiki/ (skill ingest).",
            "On each question, return the best matching compiled pages (no model in query).",
        ],
        "best_for": "Readable pages with full names; Karpathy llm-wiki pattern via the harness agent.",
    },
}


def write_html(path: Path, payload: dict) -> None:
    path.write_text(_render(payload), encoding="utf-8")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _fmt_ms(ms: float) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{ms:.1f} ms"


def _fmt_tok(n: int) -> str:
    if n >= 1000:
        return f"{n:,}"
    return str(n)


def _display_map(raw: dict) -> dict[str, dict]:
    return {
        "graph_memory": raw.get("graph_memory") or {},
        "fde_kb": raw.get("fde_kb") or {},
        "llm_wiki": raw.get("llm_wiki") or {},
    }


def _case_cell(case: dict, key: str) -> dict | None:
    return case.get(key)


def _bar_row(label: str, value: float, max_value: float, cls: str, right: str) -> str:
    pct = 0.0 if max_value <= 0 else min(100.0, (value / max_value) * 100.0)
    return (
        f'<div class="bar-row">'
        f'<span class="name">{_esc(label)}</span>'
        f'<div class="track"><i class="{cls}" style="width:{pct:.2f}%"></i></div>'
        f'<span class="n">{_esc(right)}</span>'
        f"</div>"
    )


def _mark(item: dict | None) -> str:
    if item is None:
        return '<span class="na result">-</span>'
    tok = _esc(_fmt_tok(int(item.get("tokens") or 0)))
    ms = _esc(_fmt_ms(float(item.get("ms") or 0)))
    if item.get("pass"):
        return (
            f'<div class="result">'
            f'<span class="ok">pass</span>'
            f'<span class="muted">{tok} tok</span>'
            f'<span class="muted">{ms}</span>'
            f"</div>"
        )
    return (
        f'<div class="result">'
        f'<span class="bad">miss</span>'
        f'<span class="muted">{tok} tok</span>'
        f'<span class="muted">{ms}</span>'
        f"</div>"
    )


def _run_commands_html(fast: bool, out_dir: str = "") -> str:
    del fast  # kept for call sites; bench never uses a model API
    out_line = ""
    if out_dir:
        out_line = f"<p class='muted'><strong>This run output:</strong> <code>{_esc(out_dir)}</code></p>"
    note = (
        "<p class='muted'><strong>No API key.</strong> Skills run under Poolside / Codex / Claude Code. "
        "The harness agent does llm-wiki ingest when needed. This bench only scores retrieve "
        "(or script <code>compile-extracts</code>). Pass <code>--wiki path</code> to score a wiki "
        "the agent already wrote.</p>"
    )
    return f"""
<div class="plain" id="how-to-run">
  <h4 style="margin:0 0 10px;color:var(--wiki)">Run this bench again</h4>
  <p>From the <strong>fde-lab kit root</strong>. Opens <code>benchmark.html</code> in your browser when done. Reports are written to the OS temp folder (not the git repo).</p>
  {out_line}
  <p><strong>Multi-hop demo</strong> (refund / Marcus Webb — fair push vs pull):</p>
  <pre class="cmd">py -3 scripts\\retrieval-bench.py --demo multihop
python3 scripts/retrieval-bench.py --demo multihop</pre>
  <p>Install sibling skills first (Poolside: run once after clone):</p>
  <pre class="cmd">py -3 scripts\\link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench
python3 scripts/link-skills.py --skills fde-kb,graph-memory,llm-wiki,retrieval-bench</pre>
  <p><strong>GitHub repo</strong> (private repos: sign in with <code>gh auth login</code> or Git Credential Manager first):</p>
  <pre class="cmd">py -3 scripts\\retrieval-bench.py --repo https://github.com/org/repo.git
uv run python3 scripts/retrieval-bench.py --repo https://github.com/org/repo.git</pre>
  <p><strong>Local checkout</strong> (folder you already cloned):</p>
  <pre class="cmd">py -3 scripts\\retrieval-bench.py --repo C:\\path\\to\\checkout
python3 scripts/retrieval-bench.py --repo /path/to/checkout</pre>
  <p><strong>Obsidian / fde-kb vault</strong> (<code>playbooks/</code>, <code>engagements/</code>, <code>evals/</code>, or <code>.obsidian/</code>):</p>
  <pre class="cmd">py -3 scripts\\retrieval-bench.py --repo C:\\vaults\\FDE-vault
python3 scripts/retrieval-bench.py --repo ~/vaults/FDE-vault</pre>
  <p class="muted">Default output: <code>%TEMP%\\retrieval-bench\\run-*</code> (Windows) or <code>$TMPDIR/retrieval-bench/run-*</code> (macOS). Use <code>--out DIR</code> to override.</p>
  {note}
</div>
"""


def _flow_svg() -> str:
    return """
<svg class="flow" viewBox="0 0 860 180" role="img" aria-label="Three skills compared">
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#6d7c8c"/>
    </marker>
  </defs>
  <rect x="10" y="55" width="130" height="70" rx="10" fill="#1c2530" stroke="#2a3544"/>
  <text x="75" y="85" text-anchor="middle" fill="#e8eef4" font-size="13">GitHub repo</text>
  <text x="75" y="105" text-anchor="middle" fill="#93a1b1" font-size="11">your files</text>
  <line x1="140" y1="90" x2="180" y2="90" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="180" y="55" width="130" height="70" rx="10" fill="#1c2530" stroke="#2a3544"/>
  <text x="245" y="85" text-anchor="middle" fill="#e8eef4" font-size="13">Questions</text>
  <text x="245" y="105" text-anchor="middle" fill="#93a1b1" font-size="11">auto-built</text>
  <line x1="310" y1="90" x2="350" y2="90" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="350" y="20" width="130" height="48" rx="10" fill="#12353a" stroke="#3dd6c6"/>
  <text x="415" y="50" text-anchor="middle" fill="#3dd6c6" font-size="12">graph-memory</text>
  <rect x="350" y="78" width="130" height="48" rx="10" fill="#13233f" stroke="#4c8dff"/>
  <text x="415" y="108" text-anchor="middle" fill="#4c8dff" font-size="12">fde-kb</text>
  <rect x="350" y="136" width="130" height="48" rx="10" fill="#3a3018" stroke="#f5b942"/>
  <text x="415" y="166" text-anchor="middle" fill="#f5b942" font-size="12">llm-wiki</text>
  <line x1="480" y1="44" x2="530" y2="90" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="480" y1="102" x2="530" y2="96" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="480" y1="160" x2="530" y2="102" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="530" y="55" width="130" height="70" rx="10" fill="#1c2530" stroke="#2a3544"/>
  <text x="595" y="85" text-anchor="middle" fill="#e8eef4" font-size="13">This report</text>
  <text x="595" y="105" text-anchor="middle" fill="#93a1b1" font-size="11">3 scores</text>
  <line x1="660" y1="90" x2="710" y2="90" stroke="#6d7c8c" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="710" y="55" width="90" height="70" rx="10" fill="#1c2530" stroke="#5bd68a"/>
  <text x="755" y="98" text-anchor="middle" fill="#5bd68a" font-size="13">HTML</text>
</svg>
"""


def _render(p: dict) -> str:
    repo = _esc(p.get("repo") or "")
    window = int(p.get("window") or 128000)
    raw_skills = p.get("skills") or {}
    skills = _display_map(raw_skills)
    cases = p.get("cases") or []

    ran = [meta for key, *_ in DISPLAY_SKILLS if (meta := skills.get(key) or {}).get("ran")]
    total_tok = sum(int(m.get("tokens_total") or 0) for m in ran)
    total_ms = sum(float(m.get("ms_total") or 0) for m in ran)
    best = max(ran, key=lambda m: float(m.get("accuracy") or 0), default=None)

    score_bars = []
    tok_bars = []
    time_bars = []
    explain_cards = []
    max_acc = 1.0
    max_tok = max((int(m.get("tokens_avg") or 0) for m in ran), default=1) or 1
    max_ms = max((float(m.get("ms_avg") or 0) for m in ran), default=1.0) or 1.0

    ingest = p.get("llm_wiki_ingest") or {}
    ingest_note = ""
    if ingest.get("mode"):
        bits = [_esc(str(ingest.get("mode")))]
        if ingest.get("pages") is not None:
            bits.append(f"{int(ingest['pages'])} wiki pages")
        if ingest.get("tokens_in"):
            bits.append(f"{_fmt_tok(int(ingest['tokens_in']))} ingest tokens in")
        if ingest.get("tokens_out"):
            bits.append(f"{_fmt_tok(int(ingest['tokens_out']))} out")
        if ingest.get("ms"):
            bits.append(f"{_fmt_ms(float(ingest['ms']))} ingest time")
        ingest_note = f"<p class='muted'><strong>llm-wiki ingest:</strong> {' · '.join(bits)}</p>"

    for key, title, cls in DISPLAY_SKILLS:
        meta = skills.get(key) or {}
        how = HOW_IT_WORKS[key]
        subtitle = ""
        if key == "llm_wiki" and ingest.get("mode"):
            subtitle = str(ingest.get("mode"))
        subtitle_html = (
            f'<p class="subtitle">{_esc(subtitle)}</p>' if subtitle else ""
        )
        if meta.get("ran"):
            acc = float(meta.get("accuracy") or 0) * 100
            tok_avg = int(meta.get("tokens_avg") or 0)
            tok_tot = int(meta.get("tokens_total") or tok_avg * int(meta.get("total") or 0))
            ms_avg = float(meta.get("ms_avg") or 0)
            ms_tot = float(meta.get("ms_total") or ms_avg * int(meta.get("total") or 0))
            score_bars.append(_bar_row(title, float(meta.get("accuracy") or 0), max_acc, cls, f"{acc:.0f}%"))
            tok_bars.append(_bar_row(title, float(tok_avg), float(max_tok), cls, f"{_fmt_tok(tok_avg)} avg"))
            time_bars.append(_bar_row(title, ms_avg, max_ms, cls, _fmt_ms(ms_avg)))
            explain_cards.append(
                f'<article class="card">'
                f'<div class="label {cls}">{_esc(title)}</div>'
                f"{subtitle_html}"
                f"<p><strong>{_esc(how['one_line'])}</strong></p>"
                f"<ol>{''.join(f'<li>{_esc(s)}</li>' for s in how['steps'])}</ol>"
                f'<p class="muted"><strong>Best for:</strong> {_esc(how["best_for"])}</p>'
                f'<div class="metrics">'
                f'<div class="metric"><span class="muted">Correct</span><b>{int(meta.get("passed") or 0)}/'
                f'{int(meta.get("total") or 0)}</b></div>'
                f'<div class="metric"><span class="muted">Tokens / question</span><b>{_esc(_fmt_tok(tok_avg))}</b></div>'
                f'<div class="metric"><span class="muted">Total tokens</span><b>{_esc(_fmt_tok(tok_tot))}</b></div>'
                f'<div class="metric"><span class="muted">Time / question</span><b>{_esc(_fmt_ms(ms_avg))}</b></div>'
                f'<div class="metric"><span class="muted">Total time</span><b>{_esc(_fmt_ms(ms_tot))}</b></div>'
                f'<div class="metric"><span class="muted">Of {_esc(window)} window</span>'
                f'<b>{float(meta.get("tokens_pct_window") or 0):.2f}%</b></div>'
                f'<div class="metric"><span class="muted">Answer ranked 1st</span>'
                f'<b>{int(meta.get("top1") or 0)}/{int(meta.get("ranked_cases") or 0)}</b></div>'
                f'<div class="metric"><span class="muted">MRR</span>'
                f'<b>{float(meta.get("mrr") or 0):.2f}</b></div>'
                f"</div>"
                f'<p class="muted"><strong>Ran on:</strong> {_esc(meta.get("input") or "n/a")}</p>'
                f"</article>"
            )
        else:
            skip_sub = subtitle_html or '<p class="subtitle">Not installed</p>'
            explain_cards.append(
                f'<article class="card skip">'
                f'<div class="label {cls}">{_esc(title)}</div>'
                f"{skip_sub}"
                f"<p><strong>{_esc(how['one_line'])}</strong></p>"
                f'<p class="muted">{_esc(meta.get("skip") or "skipped")}</p>'
                f"</article>"
            )

    q_rows = []
    for i, case in enumerate(cases, start=1):
        trap = ' <span class="pill">should say nothing</span>' if case.get("empty_ok") else ""
        reason = case.get("reason") or ""
        looking = ""
        if case.get("needles") and not case.get("empty_ok"):
            needle = str(case["needles"][0])
            if len(needle) > 90:
                needle = needle[:87] + "..."
            looking = f'<div class="why"><strong>Looking for:</strong> {_esc(needle)}</div>'
        why = f'<div class="why"><strong>Why asked:</strong> {_esc(reason)}</div>' if reason else ""
        cells = "".join(
            f'<td class="skill">{_mark(_case_cell(case, key))}</td>'
            for key, *_ in DISPLAY_SKILLS
        )
        q_rows.append(
            f"<tr>"
            f'<td class="num qnum">Q{i}</td>'
            f'<td class="q">{_esc(case.get("question") or "")}{trap}{why}{looking}</td>'
            f"{cells}</tr>"
        )

    winner = ""
    if best is not None:
        best_acc = float(best.get("accuracy") or 0)
        for key, title, *_ in DISPLAY_SKILLS:
            meta = skills.get(key) or {}
            if meta.get("ran") and float(meta.get("accuracy") or 0) == best_acc:
                winner = (
                    f"{title} ({int(meta.get('passed') or 0)}/{int(meta.get('total') or 0)} correct, "
                    f"MRR {float(meta.get('mrr') or 0):.2f})."
                )
                break

    # "Correct" only asks whether the answer was somewhere in the returned text.
    # The skills return very different amounts of it, and in the demo they do not
    # even read the same input. Say so, next to the score, rather than in a footnote.
    caveats = []
    inputs = {
        title: str((skills.get(key) or {}).get("input") or "")
        for key, title, *_ in DISPLAY_SKILLS
        if (skills.get(key) or {}).get("ran")
    }
    if len({v for v in inputs.values() if v}) > 1:
        pairs = " · ".join(f"<strong>{_esc(t)}</strong> ran on {_esc(v)}" for t, v in inputs.items() if v)
        caveats.append(
            "<strong>These are not the same inputs.</strong> " + pairs
            + ". A skill reading hand-modelled facts is being compared against a "
            "skill reading raw files, so the scores are not like-for-like."
        )
    if "auto-generated" in str(p.get("questions_origin") or ""):
        caveats.append(
            "<strong>The questions came from the extract.</strong> graph-memory and llm-wiki are "
            "built from that same extract, so they are being asked about their own input, while "
            "fde-kb reads the original files. Treat their scores here as a ceiling, not a forecast. "
            "Use <code>--questions</code> with a hand-written set for a decision-grade number."
        )
    caveats.append(
        "<strong>&ldquo;Correct&rdquo; is generous.</strong> It means the answer appeared anywhere in "
        "the returned text, so a skill that returns more text passes more often by luck. "
        "<strong>MRR</strong> and <strong>Answer ranked 1st</strong> are the honest precision numbers &mdash; "
        "read those first."
    )
    caveat_html = (
        '<div class="banner"><ul style="margin:0;padding-left:18px">'
        + "".join(f"<li style='margin:6px 0'>{c}</li>" for c in caveats)
        + "</ul></div>"
    )

    llm_banner = ""
    if ingest.get("mode"):
        llm_banner = f'<div class="banner ingest-banner">{ingest_note}</div>'

    summary = p.get("repo_summary") or {}
    repo_name = _esc(summary.get("name") or repo)
    repo_blurb = _esc(summary.get("blurb") or "No README summary found.")
    topics = summary.get("sample_topics") or []
    topics_html = ""
    if topics:
        items = "".join(f"<li>{_esc(t)}</li>" for t in topics)
        topics_html = f"<p class='muted'><strong>Main folders scanned:</strong></p><ul class='topics'>{items}</ul>"
    source_kind = summary.get("source_kind") or "repo"
    about_title = "What is this vault?" if source_kind == "obsidian_vault" else "What is this repo?"
    fde_kb_note = ""
    fde_meta = (p.get("skills") or {}).get("fde_kb") or {}
    if fde_meta.get("fde_kb_mode"):
        fde_kb_note = f"<p class='muted'><strong>fde-kb index mode:</strong> {_esc(fde_meta['fde_kb_mode'])}</p>"

    run_cmds = _run_commands_html(bool(p.get("fast")), str(p.get("out_dir") or ""))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Retrieval bench · {repo}</title>
<style>
:root {{
  --bg:#0f1419; --bg2:#161d26; --bg3:#1c2530; --line:#2a3544;
  --text:#e8eef4; --muted:#93a1b1; --dim:#6d7c8c;
  --graph:#3dd6c6; --kb:#4c8dff; --wiki:#f5b942;
  --ok:#5bd68a; --bad:#ff6b7a; --warn:#f0c14b;
}}
* {{ box-sizing:border-box; }}
html, body {{ margin:0; background:var(--bg); color:var(--text); }}
body {{ font:15px/1.5 "Segoe UI","Helvetica Neue",Helvetica,Arial,sans-serif; }}
.app {{ display:flex; min-height:100vh; }}
nav {{
  width:200px; flex-shrink:0; background:var(--bg2); border-right:1px solid var(--line);
  padding:22px 14px; position:sticky; top:0; height:100vh;
}}
nav h1 {{ font-size:12px; letter-spacing:0.08em; text-transform:uppercase; color:var(--muted); margin:0 0 14px; }}
nav a {{ display:block; color:var(--text); text-decoration:none; padding:8px 10px; border-radius:6px; }}
nav a:hover {{ background:var(--bg3); }}
main {{ flex:1; padding:28px 40px 72px; min-width:0; }}
.page {{ max-width:1200px; margin:0 auto; }}
h2 {{ font-size:26px; font-weight:700; margin:0 0 8px; }}
h3 {{ font-size:18px; font-weight:650; margin:34px 0 12px; }}
.sub {{ color:var(--muted); margin:0 0 18px; }}
.plain, .banner, .about {{
  background:var(--bg2); border:1px solid var(--line); border-radius:12px;
  padding:16px 20px; margin:0 0 18px;
}}
.banner {{ border-color:#3a4a22; background:#1a2218; }}
.warn {{ color:var(--warn); font-size:13px; margin:10px 0 0; }}
.subtitle {{ color:var(--muted); font-size:13px; margin:0 0 10px; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:0 0 22px; }}
.kpi {{ background:var(--bg2); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.kpi .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
.kpi .value {{ font-size:26px; font-weight:700; margin-top:6px; font-variant-numeric:tabular-nums; }}
.grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; align-items:stretch; }}
.card {{ background:var(--bg2); border:1px solid var(--line); border-radius:12px; padding:16px 18px; height:100%; display:flex; flex-direction:column; }}
.card.skip {{ opacity:0.78; }}
.card .label {{ font-size:13px; font-weight:700; margin-bottom:4px; }}
.card .label.graph {{ color:var(--graph); }}
.card .label.kb {{ color:var(--kb); }}
.card .label.wiki {{ color:var(--wiki); }}
.card ol {{ margin:8px 0 12px 18px; padding:0; color:var(--muted); flex:1; }}
.metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px 12px; margin-top:auto; padding-top:14px; border-top:1px solid var(--line); }}
.metric {{ display:flex; flex-direction:column; gap:3px; min-height:44px; }}
.metric .muted {{ font-size:11px; line-height:1.3; }}
.metric b {{ font-size:15px; font-variant-numeric:tabular-nums; line-height:1.2; }}
.muted {{ color:var(--muted); font-size:13px; }}
.chart {{ background:var(--bg2); border:1px solid var(--line); border-radius:12px; padding:16px 20px; margin:0 0 14px; }}
.bars {{ display:grid; gap:12px; }}
.bar-row {{ display:grid; grid-template-columns:140px minmax(0,1fr) 88px; gap:12px; align-items:center; min-height:24px; }}
.bar-row .name {{ color:var(--text); font-size:13px; font-weight:600; white-space:nowrap; }}
.track {{ height:18px; background:var(--bg3); border-radius:5px; overflow:hidden; min-width:0; }}
.track i {{ display:block; height:100%; border-radius:5px; min-width:2px; }}
.track i.graph {{ background:var(--graph); }}
.track i.kb {{ background:var(--kb); }}
.track i.wiki {{ background:var(--wiki); }}
.n {{ text-align:right; font-variant-numeric:tabular-nums; font-size:13px; color:var(--muted); white-space:nowrap; }}
.flow {{ width:100%; height:auto; margin:8px 0 4px; background:var(--bg2); border:1px solid var(--line); border-radius:12px; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:12px; background:var(--bg2); }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
th, td {{ text-align:left; padding:12px 14px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:0.04em; background:var(--bg3); }}
th.num, td.num {{ width:48px; text-align:center; padding-left:8px; padding-right:8px; }}
th.skill, td.skill {{ width:112px; text-align:center; vertical-align:middle; padding-left:8px; padding-right:8px; }}
th.q, td.q {{ width:auto; }}
td.q {{ word-wrap:break-word; }}
.qnum {{ color:var(--dim); font-weight:600; }}
.result {{ display:flex; flex-direction:column; align-items:center; justify-content:center; gap:2px; line-height:1.35; }}
.result .muted {{ font-size:11px; }}
.ok {{ color:var(--ok); font-weight:700; font-size:13px; }}
.bad {{ color:var(--bad); font-weight:700; font-size:13px; }}
.na {{ color:var(--dim); }}
.ingest-banner {{ margin:0 0 18px; }}
.ingest-banner p {{ margin:0; }}
.pill {{ display:inline-block; margin-left:6px; padding:2px 8px; border-radius:999px; background:#2a1c22; color:#ff9aa5; font-size:11px; }}
.why {{ color:var(--muted); font-size:12px; margin-top:8px; line-height:1.45; }}
.topics {{ margin:6px 0 0 18px; color:var(--muted); font-size:13px; }}
.notes-list {{ margin:8px 0 0 18px; color:var(--muted); font-size:13px; }}
.notes-list li {{ margin:6px 0; }}
.about h4 {{ margin:0 0 8px; font-size:14px; color:var(--wiki); }}
.cmd {{ background:var(--bg3); border:1px solid var(--line); border-radius:8px; padding:12px 14px; overflow-x:auto; font-size:12px; line-height:1.5; margin:8px 0 14px; white-space:pre-wrap; }}
.cmd code, .plain code {{ font-family:ui-monospace,"Cascadia Code","Segoe UI Mono",monospace; font-size:12px; }}
@media (max-width:1100px) {{ .grid {{ grid-template-columns:1fr; }} .kpis {{ grid-template-columns:repeat(2,1fr); }} }}
@media (max-width:900px) {{ .app {{ display:block; }} nav {{ position:static; height:auto; width:auto; }} .bar-row {{ grid-template-columns:1fr; }} main {{ padding:20px; }} }}
</style>
</head>
<body>
<div class="app">
<nav>
<h1>Guide</h1>
<a href="#how-to-run">Run again</a>
<a href="#about">About this repo</a>
<a href="#start">Summary</a>
<a href="#charts">Scores</a>
<a href="#timing-notes">Timing notes</a>
<a href="#skills">The 3 skills</a>
<a href="#questions">Questions</a>
</nav>
<main>
<div class="page">
<h2 id="start">Retrieval bench</h2>
<p class="sub">Source: {repo}</p>

<div class="about" id="about">
  <h4>{_esc(about_title)}</h4>
  <p><strong>{repo_name}</strong> — {repo_blurb}</p>
  {topics_html}
  {fde_kb_note}
  <p class="muted" style="margin-top:12px">
    Questions below are <strong>auto-generated</strong> from headings, names, and contacts found in those files.
    They are a smoke test, not hand-written exam questions.
  </p>
</div>

<div class="plain">
  <p><strong>Three skills, same questions:</strong> graph-memory, fde-kb, llm-wiki.</p>
  <p><strong>Tokens</strong> = size of text pasted into the next chat turn. <strong>Time</strong> = lookup time after index/build. No model API in this bench; harness agents do llm-wiki ingest.</p>
  <p><strong>Best this run:</strong> {_esc(winner or "n/a")}</p>
</div>

{caveat_html}

{llm_banner}

{run_cmds}

{_flow_svg()}

<h3 id="charts">Scores (3 skills)</h3>
<div class="chart">
  <div class="muted" style="margin-bottom:10px">Correct answers</div>
  <div class="bars">{"".join(score_bars)}</div>
</div>
<div class="chart">
  <div class="muted" style="margin-bottom:10px">Tokens per question (lower is cheaper)</div>
  <div class="bars">{"".join(tok_bars)}</div>
</div>
<div class="chart">
  <div class="muted" style="margin-bottom:10px">Time per question (lower is faster)</div>
  <div class="bars">{"".join(time_bars)}</div>
</div>

<div class="plain" id="timing-notes">
  <p><strong>How to read “time per question”</strong></p>
  <ul class="notes-list">
    <li>Times are <strong>lookup only</strong>, after one-time index/build. Index time is not in this chart.</li>
    <li><strong>fde-kb</strong> is often faster (~1 ms): one SQLite full-text search per question, connection stays open.</li>
    <li><strong>graph-memory</strong> is often slower (~30 ms): opens the DB, scans every entity/alias name, then walks links. Still instant to a human.</li>
    <li><strong>Faster does not mean better.</strong> fde-kb can keyword-match the wrong chunk quickly. graph-memory usually returns fewer, structured facts.</li>
    <li>For Lugana / Poolside, <strong>tokens pasted</strong> matter more than these milliseconds.</li>
  </ul>
</div>

<div class="kpis">
  <div class="kpi"><div class="label">Files scanned</div><div class="value">{_esc(p.get("files") or 0)}</div></div>
  <div class="kpi"><div class="label">Questions</div><div class="value">{_esc(p.get("questions") or 0)}</div></div>
  <div class="kpi"><div class="label">Total tokens pasted</div><div class="value">{_esc(_fmt_tok(total_tok))}</div></div>
  <div class="kpi"><div class="label">Total lookup time</div><div class="value">{_esc(_fmt_ms(total_ms))}</div></div>
</div>

<h3 id="skills">The 3 skills</h3>
<div class="grid">{"".join(explain_cards)}</div>

<h3 id="questions">Each question</h3>
<p class="sub">pass = expected text showed up in what the skill returned. Each row explains why the question exists.</p>
<div class="table-wrap">
<table>
<colgroup>
  <col style="width:48px"/>
  <col/>
  <col style="width:112px"/>
  <col style="width:112px"/>
  <col style="width:112px"/>
</colgroup>
<thead>
<tr>
  <th class="num">#</th>
  <th class="q">Question · why · expected text</th>
  <th class="skill">graph-memory</th>
  <th class="skill">fde-kb</th>
  <th class="skill">llm-wiki</th>
</tr>
</thead>
<tbody>{"".join(q_rows)}</tbody>
</table>
</div>

</div>
</main>
</div>
</body>
</html>
"""
