---
name: p6-native-import
description: Build a native Oracle Primavera P6 import file (XER) from CSV sources, and verify what P6 actually stored. Use when creating, fixing or debugging a P6 import, converting CSV or Excel schedules into XER, automating a P6 load through the command line, or reading a programme back out of a P6 database. Covers P6 Professional and P6 EPPM import files.
agent_created: true
---

# Native P6 import

Produce an XER that Primavera P6 accepts on the first attempt, and prove what landed.

## When to use

- "Make me a P6 import file" / "convert this schedule to XER" / "load this into P6"
- A P6 import failed, partially imported, or produced shifted dates
- Automating a P6 load headlessly (command line, scheduled task, agent)
- Verifying that a stored P6 programme matches its source

## The three things that actually break imports

Everything else is bookkeeping. These three are where the time goes.

**1. Encoding.** P6's XER importer uses the Windows code page, not UTF-8. A UTF-8 file containing
any non-ASCII character imports with mojibake, or fails. Write CP1252, and fail loudly on
unencodable characters rather than substituting silently.

**2. The header.** `ERMHDR` has **nine** tab-separated fields in a fixed order. A tenth field
shifts the currency into the wrong position and P6 rejects the whole file:

```
ERMHDR  22.12.0  2026-07-25  Project  ADMIN  user  dbxDatabaseNoName  Project Management  AED
        version   date       kind     user   ?     database           ?                   currency
```

**3. The calendar.** Durations are stored in **hours**, as `days × hours_per_working_day`, where
hours-per-day comes from the calendar's working-time blob — *not* from its declared `day_hr_cnt`,
which is frequently inconsistent with it. Trusting the declared value rescales every activity
silently, with no error anywhere.

## Workflow

### Step 1 — build

```bash
python scripts/p6native.py build --pack <csv-dir> --out programme.xer --project MY-PROJECT
```

Reads three files from the pack:

| File | Columns used |
|---|---|
| `activities.csv` | `activity_id`, `name`, `wbs`, `duration` (days), `start`, `finish` (`YYYY-MM-DD`) |
| `relationships.csv` | `predecessor`, `successor`, `type` (`FS`/`SS`/`FF`/`SF`), `lag` (days) |
| `wbs.csv` | `code`, `name`, `level`, `parent` |

The build patches `assets/skeleton.xer` — a file exported by P6 itself — rather than synthesising
one. The skeleton supplies the header, the calendar blob, the OBS node and every table's field
list; the script supplies the rows. Do not hand-write field lists: they are the part P6 is least
forgiving about, and the skeleton already has them right.

### Step 2 — check before importing

```bash
python scripts/p6native.py check --xer programme.xer --pack <csv-dir>
```

Twelve checks: header field count, required tables, row arity, WBS tree, unique task codes, orphan
tasks, resolvable links, calendar consistency, duration arithmetic, and three equivalence checks
against the source CSVs. A failure here costs seconds; a failure inside P6 costs an import cycle.

### Step 3 — import

Close P6 Professional first — with it open, the command silently launches the GUI instead of
executing.

```bash
python scripts/p6native.py actions --xer programme.xer --out actions.xml --eps MY-EPS
"<P6 install>\Primavera.CacheService.exe" /username=admin /password=<pw> /alias=<alias> \
    /actionScript=actions.xml /pmlogfile=import.log
```

`/alias` must name a database connection that already exists in the P6 client. Creating one is a
GUI action (Database Configuration); it cannot be created from the command line. Exit code 0 means
success; anything else means read `import.log`, because the launcher can mask P6's own codes.

### Step 4 — verify what P6 stored

```bash
python scripts/p6native.py verify --db "<P6>/PPMDBSQLite.db" --project MY-PROJECT --pack <csv-dir>
```

Reads the database read-only and compares activity set, WBS count and link count against the
source. An import that reports success with 240 of 244 activities is a failure.

For full confidence, export the project from P6 and run `check` against that export: P6 rewrites
many fields on import, so a passing round-trip is the real proof.

## Non-negotiables

- **Never write to a P6 database directly.** Go through XER or the P6 CLI. P6 stores derived data
  across many tables, and a partial write corrupts silently.
- **XER import is create-only.** It cannot update an existing project; re-importing a revision
  creates a duplicate. Decide the delete-then-create policy before the first load.
- **Never report float or critical path from an uncalculated file.** Those are only authoritative
  after P6 schedules the project (F9).
- **Label reconstructions as reconstructions.** If the source is a derived or re-baselined model
  rather than an approved baseline, say so in the deliverable.

## Reference files

- `references/xer-contract.md` — header positions, minimum tables, per-table field lists,
  enumerations, calendar blob grammar, what P6 rewrites.
- `references/failure-modes.md` — four real import failures: symptom, cause, fix.
- `references/acceptance-loop.md` — the full build → import → readback → re-export loop, with gates.
