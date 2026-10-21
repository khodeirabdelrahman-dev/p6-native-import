# Failure modes

Six ways a P6 import fails. The first four are from a real load that took four attempts to land.

## 1. Header field count — P6 rejects the whole file

**Symptom.** Import fails immediately. The launcher returns a code that does not match P6's own
documented codes, or returns 0 while nothing lands.

**Cause.** A tenth field in `ERMHDR`. Everything after it shifts one position left, so
`Project Management` sits where the currency belongs.

**Fix.** Nine fields exactly. When the exit code looks wrong, read `pmlogfile` — the launcher can
mask P6's real code.

## 2. Encoding — imports, but the text is wrong

**Symptom.** The project imports, activity count is right, and names contain `Ã©`, `Â°`, or
question marks where accented characters should be.

**Cause.** The file was written UTF-8. P6's importer decodes with the Windows code page.

**Fix.** Write CP1252. Encode strictly so an unrepresentable character raises at build time with
the offending value named, rather than becoming a silent substitution.

## 3. P6 already open — nothing happens

**Symptom.** The command returns without an error, no log is written, and the project is absent.

**Cause.** With the GUI open, the launcher opens the application instead of executing the script.

**Fix.** Check that P6 is closed before running, and always assert on the log file's existence and
content — not only on the exit code.

## 4. Calendar units — everything shifts, nothing errors

**Symptom.** The project imports cleanly. Every date is wrong by a consistent factor.

**Cause.** Durations were converted using the calendar's declared `day_hr_cnt`, which contradicted
its own working-time blob. A calendar declaring `10` hours/day whose windows describe 8 rescales
every duration by 25%.

**Fix.** Derive hours-per-day from the `clndr_data` windows, convert `days × hours_per_day`, and
write back consistent `day_hr_cnt` / `week_hr_cnt` / `year_hr_cnt`.

## 5. Missing `obs_id` — rejected by strict parsers, tolerated by P6

**Symptom.** P6 imports fine; a third-party XER parser raises on a null field.

**Cause.** Minimal hand-built files often omit `PROJWBS.obs_id`. Every WBS node must belong to an
OBS node, and P6 populates this on every row it writes.

**Fix.** Emit an OBS table and set `obs_id` on every WBS row. Do not infer the import contract
from database `NOT NULL` constraints — SQLite enforces none, and P6 validates in its own layer.

## 6. Duplicate project — re-import instead of update

**Symptom.** After a revision, P6 holds two projects with near-identical names.

**Cause.** XER import is create-only. There is no update path through the CLI.

**Fix.** Decide the policy before the first load: delete-then-create, or a distinct name per
revision with the superseded one archived. Never leave two live projects claiming to be current.

## Verification traps

- **Counts are not equivalence.** 244 activities imported is not 244 activities correct. Compare
  activity codes, names, dates and durations.
- **Exit code 0 is not success.** Assert the log and the readback.
- **A passing check on your own file proves only self-consistency.** The real proof is exporting
  the project from P6 and checking that export against the source.
