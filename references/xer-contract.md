# XER contract

Everything here was read off a file that P6 Professional 22.12 accepted and then re-exported
itself, not from documentation.

## Encoding and line endings

- **CP1252**, not UTF-8. Decode and encode as `cp1252`.
- P6's own export uses CRLF; a file with LF is also accepted.
- Tab-separated. A tab or newline inside a value corrupts the row — reject such values at build
  time.

## Header

Nine fields, tab-separated, first line:

```
ERMHDR <version> <date> Project <user> <user2> dbxDatabaseNoName Project Management <currency>
```

| # | Field | Accepted value |
|---|---|---|
| 1 | literal | `ERMHDR` |
| 2 | version | `22.12.0` |
| 3 | date | `YYYY-MM-DD` |
| 4 | kind | `Project` |
| 5 | user | `ADMIN` |
| 6 | user 2 | `user` |
| 7 | database | `dbxDatabaseNoName` |
| 8 | module | `Project Management` |
| 9 | currency | `AED` |

A tenth field pushes `Project Management` into the currency position and P6 rejects the file.

## Record markers

```
%T <table>       start table
%F <f1> <f2>...  field names for this table
%R <v1> <v2>...  one row
%E               end of file
```

Rows must have exactly as many values as the field list declares.

## Minimum tables

Six tables are enough for an import. P6 fills the rest and exports eight.

| Table | Import | P6 export | Rows |
|---|---|---|---|
| PROJECT | 71 fields | 71 | 1 |
| CALENDAR | 13 | 13 | 1 |
| OBS | 10 | 6 | ≥1 |
| PROJWBS | 25 | 26 | ≥1 (exactly one root) |
| TASK | 25 | 61 | n |
| TASKPRED | 8 | 11 | n |
| CURRTYPE | not required | 11 | 17 |
| FINTMPL | not required | 3 | 1 |

## Enumerations that matter

These are the values P6 22.12 writes. Several plausible-looking variants are silently coerced or
rejected — use these exactly.

| Field | Value |
|---|---|
| `TASK.task_type` | `TT_Task`, `TT_Mile` (zero duration), `TT_FinMile` |
| `TASK.status_code` | `TK_NotStart`, `TK_Active`, `TK_Complete` |
| `TASK.complete_pct_type` | `CP_Drtn` (not `CP_Duration`) |
| `TASK.duration_type` | `DT_FixedDUR2` (not `DT_FixedDrtn`) |
| `PROJWBS.status_code` | `WS_Open` |
| `TASKPRED.pred_type` | `PR_FS`, `PR_SS`, `PR_FF`, `PR_SF` |
| `PROJWBS.ev_compute_type` | `EC_Cmp_pct` |
| `PROJWBS.ev_etc_compute_type` | `EE_Rem_hr` |
| `CALENDAR.clndr_type` | `CA_Project` |

## Dates and durations

- Format `YYYY-MM-DD HH:MM`.
- Start `08:00`, finish `17:00` for an 8-hour day.
- Durations are **hours** in `target_drtn_hr_cnt` and `remain_drtn_hr_cnt`.
- Milestones: `TT_Mile` with `0` hours, start and finish on the same day.
- Lags are **hours** in `TASKPRED.lag_hr_cnt`.
- Leave `early_*`, `late_*`, `*_float_*` and `act_*` **empty** — P6 computes them when the project
  is scheduled. Writing them invites contradictions.

## Calendar

`clndr_data` is a small nested grammar:

```
(0||CalendarData()(
   (0||DaysOfWeek()(
      (0||1()())                                     <- day 1 (Sunday): no windows, non-working
      (0||2()((0||0(s|08:00|f|12:00)())(0||1(s|13:00|f|17:00)())))
      ... days 3..7
   ))
   (0||Exceptions()())
))
```

Days are `1`=Sunday … `7`=Saturday. Each window is `s|HH:MM|f|HH:MM`. Parse the windows to derive
real working hours per day and the number of working days; ignore `day_hr_cnt` when the two
disagree, then **write back a consistent `day_hr_cnt` / `week_hr_cnt` / `year_hr_cnt`**. A calendar
named "6D 8H" that declares `10` / `60` is a live example of the inconsistency.

## What P6 rewrites on import

Expect the stored project to differ from the file in these ways — none are errors:

- Project-root WBS short name is rewritten to follow the project identifier.
- Fields left empty are populated with computed or default values (dates, float, EV fields).
- Additional tables and columns appear on re-export.
- `proj_id`, `task_id`, `wbs_id` are reassigned; the file's ids are local to the file.

Compare against the source **by activity code and WBS code**, never by id.
