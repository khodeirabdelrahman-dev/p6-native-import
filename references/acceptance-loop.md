# Acceptance loop

The loop that turns "it imported" into "it is correct". Each gate must pass before the next step is
worth running.

```
build ──▶ check ──▶ import ──▶ readback ──▶ re-export ──▶ check export
  │         │          │          │             │              │
  └── fail ─┴── fail ──┴── fail ──┴─── fail ────┴──── fail ────┘
```

## Gates

| Gate | Question | Evidence |
|---|---|---|
| G1 build | Did a file get produced, deterministically? | sha256 of the output, stable across two builds |
| G2 check | Is it structurally valid and equivalent to the source? | 12 checks, 0 failures |
| G3 preflight | Is P6 closed, and does the alias exist? | no `PM.exe` process; alias named |
| G4 import | Did P6 accept it? | exit code **and** a completion log naming the project |
| G5 readback | Does the database hold what was sent? | activity set, WBS count, link count |
| G6 round-trip | Does P6's own export agree? | `check` on the exported file |

Stop at the first failure. A later gate cannot repair an earlier one.

## Determinism

Build twice and compare digests. Identical bytes mean the file is a function of the source, not of
row order, dictionary order or the clock. If they differ, find the nondeterminism before importing —
an import you cannot reproduce is an import you cannot audit.

## Reading the result

Three outcomes, and they are not interchangeable:

- **IMPORTED** — P6 accepted the file. Says nothing about correctness.
- **VERIFIED** — the database matches the source on the fields that matter.
- **ROUND-TRIPPED** — P6's own re-export passes the same checks. This is the only outcome worth
  calling acceptance.

Record which one applies. "Imported" presented as "verified" is the failure mode that survives
longest, because the evidence looks fine until someone opens the schedule.

## Prerequisites that cannot be scripted

- **Database alias.** The CLI takes `/alias=<name>`; the alias must already exist in the P6 client.
  Create it once via Database Configuration.
- **P6 closed.** No API for this — check the process list.
- **A scratch target.** Import into a throwaway database before touching a live one. The import is
  create-only, so a bad load leaves a duplicate project behind.

## What to keep

For each run: the source pack, the XER, its sha256, the action script, the P6 log, the readback
result, and the re-export. Anything less and the claim cannot be re-checked later.
