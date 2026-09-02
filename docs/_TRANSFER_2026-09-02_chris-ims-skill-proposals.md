# TEMPORARY TRANSFER — DELETE AFTER FETCH

| Field | Value |
| --- | --- |
| Status | **TEMPORARY** — remove after pull on work laptop |
| Trace id | `xfer-2026-09-02-chris-ims-skills` |
| Created | 2026-09-02 |
| Repo | `eminbayrak/ai-architecture` |
| Path | `docs/_TRANSFER_2026-09-02_chris-ims-skill-proposals.md` |
| Source | Kickoff call notes (Chris / IMS) + skill proposal draft |
| Audience | Emma / FDE — personal transfer only |
| Cleanup | Delete this file (and commit) once copied to work machine |

---

## What Chris actually asked for

Strip the rambling. He named four things:

1. **"I would love to clone myself"** — no second pair of eyes, no reviewer, he is the sole developer.
2. **"It's hard for me to validate that everything I'm doing is right"** — he finds out he is wrong when Mitch reports a bad number.
3. **"Make me more productive"** — he already uses Poolside for SQL refactors and pulls npm skills from the wild.
4. **Line of Balance is still manually curated** and he knows it should not be.

Everything below maps to one of those.

---

## Tier 1: ship first, no business sign-off needed, low risk

### 1. Pipeline sentinel (data quality regression harness)

Generates dbt tests plus a run-over-run anomaly report on his gold tables. Row count deltas, null spikes in key columns, part numbers that appeared or vanished, inventory totals swinging past a threshold, orphan part numbers not in part master, plant codes not in plant master.

**The killer check:** an unclassified bucket. His SAP receipt and usage logic maps movement type + plant + storage location into business categories. Any combination that does not match a known rule gets flagged loudly instead of silently dropped. That is exactly where the "quirkiest data issues" Mitch finds live.

This flips his failure mode. Today the pipeline is quiet and a human catches it. Tomorrow the pipeline complains before anyone sees a bad number.

### 2. Reverse documentation (SQL to Confluence)

He got caught live on the call with the Confluence page not updated. A skill that parses each SQL and dbt model and emits the business rules in plain English: source tables, filters, the movement type and plant and storage location rules, output column definitions, lineage. Runs on every merge so docs never drift again.

Second-order benefit that matters more than the docs: this becomes the context file that makes every other AI tool on his codebase smarter, including the Q&A layer later.

### 3. Domain-aware code review

Not a linter. Encode the traps specific to this program:

- Did this join fan out? What is the grain, part x plant x date?
- Does this filter correctly separate global spares pool from deployment spares?
- Is government-owned inventory mixed with contractor-owned?
- Is plant 1133 handled as central inventory everywhere it should be?
- Are unserviceable (HFFR) parts excluded from serviceable counts?

This is the literal answer to "look over my shoulder and tell me what I'm missing."

### 4. Power BI to Databricks logic extraction

He said roughly 20% of transforms are still stuck in DAX and he does not like it ("we don't want to lock business logic into dashboards"). A skill that inventories the pbix measures, splits presentation logic from business logic, and drafts the dbt model to push the business logic down into gold. Shrinks his untested, unversioned surface area.

---

## Tier 2: the business wins

### 5. Line of Balance engine, built backwards from the manual sheet

Do not build this first and do not build it forward. Build it as a reconciliation:

- Implement both rate methodologies as explicit parameterized calcs (the P-BOM rates he used originally, and the fallout rate the planners actually want). Let them run side by side instead of arguing about which is right.
- Project to monthly grain per part out to the current horizon.
- Run it against the manually curated spreadsheet for three or four constrained parts and produce a line by line variance explanation.

That variance report is the deliverable, not the LOB. It is what earns enough trust to retire the manual sheet, and it will surface the assumptions nobody wrote down. Production demand gets folded in as a second input source once that data actually exists.

### 6. P-BOM ingest guardrail

Mitch called the P-BOM "the heartbeat of our depot planning cycle" and it is an Excel file a human uploads. Validate it on ingest: schema check, rates bounded 0 to 100, a diff report on any rate that moved more than X% since the last version, new and removed part numbers, orphan parts against part master. Add version stamping so anyone can answer "which P-BOM version produced this LOB?"

Tiny effort. Protects the single most critical input in the program.

### 7. Constrained part triage brief

For a part like Roto 9, generate a one-pager on demand: on-hand by plant category, unserviceable pool and where it is sitting, open orders and which are late against contract dates, repair WIP and expected yield, projected monthly gap, and candidate mitigations with specifics (N serviceable units at plant X that could be redistributed, M unserviceable units at unit-level plants not returned in 90+ days, supplier commit delta versus original plan).

This turns Chris's "I'm looking at this and going holy crap" into a repeatable artifact. It is also directly Mitch's job, since he builds the leadership reports by hand today. Two stakeholders, one skill.

### 8. Stuck asset finder

Narrow and immediately demonstrable. Chris named feeding the repair stream as one of their biggest issues, with unserviceable parts scattered worldwide and not shipping back. Report: unserviceable parts aging past a threshold at unit plants, in-transit parts with no movement in N days, parts sitting at NELC or SMLC that never transferred to central inventory.

Small query. Visible operational action within a week. Good candidate for the demo that buys you room for the bigger work.

---

## Tier 3: after the foundation exists

### 9. Natural language Q&A over the gold tables

The idea Kevin floated. Only viable after #2 exists, because it needs a semantic layer. Scope it to fixed question shapes first (part status, where is my inventory, LOB for part X) rather than open-ended text to SQL, which will embarrass you on a program like this.

### 10. Bus factor package

Runbooks, dependency map from ingest through dbt to gold to Power BI, onboarding doc. Chris being the only person who understands this is the real program risk, and it is a risk his leadership will fund.

### 11. Production integration prep

Do the mapping doc and data contract before anyone writes code. Chris owes you contacts (he mentioned Crystal DeBrandt, possibly Rob Tarka or the production chief). Chase that.

---

## Recommendation on sequencing

Start with **1** and **2** together. They need no business approval, no stakeholder alignment, and no agreement on contested definitions. They directly answer his stated pain. And #2 produces the context asset every later skill depends on.

Then **8** as a fast visible win, then **5** as the flagship.

Do **not** lead with Line of Balance even though it is the biggest prize. He cannot yet tell you which rate methodology is correct, and the production data set does not exist in his pipeline. You would be building on sand.

---

## What to get from Chris this week

- Confluence link (he offered)
- Repo read access, specifically the refactor branch heading to prod
- Databricks QA schema access
- Sample P-BOM, sample aggregate demand file, and one manually curated LOB spreadsheet (that last one is your validation ground truth)
- Production side contacts
- Which three constrained parts to use as test cases
- A session with Mitch to catalogue every data bug he has found historically. That list is your test suite, and it is the fastest path to a sentinel that catches real problems instead of theoretical ones.

---

## Call context (compact)

| Topic | Notes |
| --- | --- |
| Mission | Fleet readiness: parts on shelf before units/depots need them |
| Part supply paths | New buy, repair/overhaul, residual-life reuse across modules |
| Scale | Hundreds of plants (units, depots) worldwide; lots of shipping |
| Enterprise data | SAP primary; Giles for unit-level receipts/usages (source of truth when SAP messaging breaks) |
| Manual inputs (Excel → Databricks) | Aggregate demand, P-BOM / Deep Look, part status (depot + unit flags), master commits, plant master, part master |
| IMS | Power BI dashboard; read-only status view; no user input; ~80% transforms in Databricks gold, ~20% in Power BI |
| P-BOM | Depot-maintenance focused rates (not full engine BOM); critical for depot planning, supplier buys, stock positioning |
| Line of Balance | Still manually curated; rate method contested (P-BOM vs fallout); production demand not yet folded in; horizon ~18–24 months |
| Pain parts | e.g. Roto 9 — long lead, hard to repair/produce, large LOB gaps |
| Chris ask | Second pair of eyes / validation, productivity via skills+agents, not a full rewrite of the old app |
| Next | Map data + business processes; plan skills one problem at a time; start where Chris needs help most |

---

## End of transfer file

Delete after successful copy to work laptop.
Trace id: `xfer-2026-09-02_chris-ims-skills`
