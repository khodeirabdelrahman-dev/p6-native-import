# p6-native-import

Build a **native Oracle Primavera P6 import file** (XER) from plain CSV — and verify what P6
actually stored afterwards.

Most "CSV to P6" attempts fail on details that produce no error message, or worse, produce a
schedule that imports cleanly with every date silently wrong. This is a small, dependency-free
tool plus the contract notes that stop that happening.

```bash
python scripts/p6native.py build --pack ./my-schedule --out programme.xer --project MY-PROJECT
python scripts/p6native.py check --xer programme.xer --pack ./my-schedule
```

```
  PASS  S01  9 header fields (expected 9)
  PASS  S02  all present
  PASS  S04  36 nodes, 1 root(s)
  PASS  S09  0 duration mismatch(es)
  PASS  E03  0 missing, 0 extra

  12/12 -> PASS
```

## Why this exists

Four attempts to land one 244-activity schedule in P6 Professional 22.12. Every failure was
invisible until it was diagnosed:

| Attempt | What happened | Cause |
|---|---|---|
| 1 | P6 rejected the file | a tenth `ERMHDR` field shifted the currency |
| 2 | imported, text corrupted | written UTF-8; P6's importer uses the Windows code page |
| 3 | nothing happened | P6 was open; the launcher opened the GUI instead |
| 4 | **verified** | CP1252, nine header fields, calendar resolved from its working-time blob |

Attempt 4 imported 244 activities, 36 WBS nodes and 315 relationships, read them back from the
database with zero mismatches, and P6's own re-export of the project passed the same checks.

This repository is that working configuration, extracted so it does not have to be rediscovered.

## The three things that actually break imports

**1. Encoding.** CP1252, not UTF-8. The build fails loudly on an unrepresentable character rather
than substituting silently.

**2. The header.** Nine tab-separated fields, fixed order. A tenth shifts the currency and P6
rejects everything.

**3. The calendar.** Durations are hours, as `days × hours_per_working_day`. Hours-per-day comes
from the calendar's working-time blob, *not* its declared `day_hr_cnt` — which is frequently
inconsistent with it. Trusting the declared value rescales every activity by the same factor with
no error anywhere.

Full detail in [`references/xer-contract.md`](references/xer-contract.md).

## How it builds

It does not synthesise an XER. It patches `assets/skeleton.xer` — a file exported by P6 itself —
which supplies the header, the calendar blob, the OBS node and every table's field list. The
script supplies only the rows.

That is deliberate: field lists and the working-time blob are the parts most likely to be wrong
when generated from scratch, and exactly the parts P6 is least forgiving about. The skeleton is
3.4 KB and you can read it.

The output is byte-identical to the file P6 accepted, apart from the project name.

## Commands

| Command | Purpose |
|---|---|
| `build` | CSV pack → XER |
| `check` | 12 structural and equivalence checks against the source |
| `actions` | write the P6 command-line action script for the import |
| `verify` | read the stored project back from a P6 SQLite database |

`build` reads `activities.csv`, `relationships.csv` and `wbs.csv`:

| File | Columns used |
|---|---|
| `activities.csv` | `activity_id`, `name`, `wbs`, `duration` (days), `start`, `finish` (`YYYY-MM-DD`) |
| `relationships.csv` | `predecessor`, `successor`, `type` (`FS`/`SS`/`FF`/`SF`), `lag` (days) |
| `wbs.csv` | `code`, `name`, `level`, `parent` |

## Importing

Close P6 Professional first — with it open, the command silently launches the GUI instead of
executing.

```bash
python scripts/p6native.py actions --xer programme.xer --out actions.xml --eps MY-EPS

"<P6 install>\Primavera.CacheService.exe" /username=admin /password=<pw> /alias=<alias> \
    /actionScript=actions.xml /pmlogfile=import.log
```

The `/alias` must name a database connection that already exists in the P6 client. Creating one is
a GUI action (Database Configuration); it cannot be created from the command line.

Then verify, and ideally round-trip:

```bash
python scripts/p6native.py verify --db "<P6>/PPMDBSQLite.db" --project MY-PROJECT --pack ./my-schedule
```

## Using it as an agent skill

The repository is structured as a [WorkBuddy / Claude-style skill](SKILL.md): `SKILL.md` plus
`scripts/`, `references/` and `assets/`. Copy the directory into your skills folder:

```bash
git clone https://github.com/khodeirabdelrahman-dev/p6-native-import.git \
  ~/.workbuddy/skills/p6-native-import
```

Or just use the script directly — it has no dependencies beyond Python 3.10+ and the standard
library.

## What this does not do

- **It will not update an existing project.** XER import is create-only. Re-importing a revision
  creates a duplicate; decide your delete-then-create policy first.
- **It does not schedule.** Float and critical path are only authoritative after P6 runs its own
  scheduler (F9). The build deliberately leaves `early_*`, `late_*`, `float` and `act_*` empty.
- **It does not write to the database.** Everything goes through XER or the supported command
  line. Direct writes to a P6 database corrupt derived data silently.
- **It does not model resources or costs.** Activity and logic network only.

## Requirements

Python 3.10+. No third-party packages. Tested against P6 Professional 22.12 on Windows; the XER
contract is stable across recent P6 versions, but the skeleton carries version-specific field
lists — re-export one from your own P6 if you target a different release.

## Licence

MIT — see [LICENSE](LICENSE).
