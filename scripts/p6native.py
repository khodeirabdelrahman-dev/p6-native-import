#!/usr/bin/env python3
"""p6native - build, check and verify a native Primavera P6 XER file.

Standard library only. Designed to be run from a skill, a shell, or an agent.

    p6native.py build   --pack <dir> --out out.xer [--project NAME] [--data-date YYYY-MM-DD]
    p6native.py check   --xer out.xer --pack <dir>
    p6native.py actions --xer out.xer --out actions.xml [--eps EPS]
    p6native.py verify  --db PPMDBSQLite.db --project NAME --pack <dir>

The XER is built by patching `assets/skeleton.xer`, a file exported by P6 itself. The skeleton
supplies the header, the calendar blob, the OBS node and every table's field list; this script
supplies the rows. That ordering matters: field lists and the working-time blob are the parts most
likely to be wrong when generated from scratch, and they are exactly the parts P6 is least
forgiving about.

Two facts are encoded here that cost four failed imports to learn:
  * the file is CP1252, not UTF-8 - P6's importer uses the Windows code page;
  * the ERMHDR has nine fields in a fixed order, and a tenth field shifts the currency.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

SKELETON = Path(__file__).resolve().parent.parent / "assets" / "skeleton.xer"

SOURCE_FILES = ["activities.csv", "relationships.csv", "wbs.csv"]

# P6 22.12 enumerations, read off a file that P6 accepted and re-exported.
TASK_DEFAULTS = {
    "proj_id": "1",
    "clndr_id": "1",
    "status_code": "TK_NotStart",
    "phys_complete_pct": "0",
    "complete_pct_type": "CP_Drtn",
    "duration_type": "DT_FixedDUR2",
    "act_start_date": "",
    "act_end_date": "",
    "early_start_date": "",
    "early_end_date": "",
    "late_start_date": "",
    "late_end_date": "",
    "total_float_hr_cnt": "",
    "free_float_hr_cnt": "",
    "float_path": "",
    "float_path_order": "",
}

WBS_DEFAULTS = {
    "proj_id": "1",
    "obs_id": "1",
    "est_wt": "1",
    "sum_data_flag": "N",
    "status_code": "WS_Open",
    "phase_id": "",
    "ev_user_pct": "0",
    "ev_etc_user_value": "0",
    "ev_compute_type": "EC_Cmp_pct",
    "ev_etc_compute_type": "EE_Rem_hr",
    "guid": "",
    "tmpl_guid": "",
}

PRED_DEFAULTS = {
    "proj_id": "1",
    "pred_proj_id": "1",
    "comments": "",
}

REL_TYPES = {"FS": "PR_FS", "SS": "PR_SS", "FF": "PR_FF", "SF": "PR_SF"}


# --------------------------------------------------------------------------- XER i/o


def read_xer(path: Path):
    raw = path.read_bytes()
    try:
        text = raw.decode("cp1252")
    except UnicodeDecodeError:
        text = raw.decode("utf-8")
    lines = text.splitlines()
    header = lines[0] if lines and lines[0].startswith("ERMHDR") else ""
    tables: dict[str, tuple[list[str], list[list[str]]]] = {}
    cur, fields, rows = None, [], []
    for line in lines[1:]:
        parts = line.split("\t")
        if parts[0] == "%T":
            if cur:
                tables[cur] = (fields, rows)
            cur, fields, rows = parts[1], [], []
        elif parts[0] == "%F":
            fields = parts[1:]
        elif parts[0] == "%R":
            rows.append(parts[1:])
        elif parts[0] == "%E":
            if cur:
                tables[cur] = (fields, rows)
            break
    return header, tables


def write_xer(path: Path, header: str, tables: dict, order: list[str]) -> bytes:
    lines = [header]
    for name in order:
        if name not in tables:
            continue
        fields, rows = tables[name]
        lines.append("%T\t" + name)
        lines.append("%F\t" + "\t".join(fields))
        for row in rows:
            padded = list(row) + [""] * (len(fields) - len(row))
            lines.append("%R\t" + "\t".join(padded))
    lines.append("%E")
    payload = "\n".join(lines) + "\n"
    try:
        data = payload.encode("cp1252")
    except UnicodeEncodeError as exc:
        bad = payload[exc.start:exc.end]
        raise SystemExit(
            f"value not representable in CP1252: {bad!r} at offset {exc.start}. "
            "P6's importer uses the Windows code page; transliterate or replace the character."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


# --------------------------------------------------------------------------- calendar


def calendar_hours(cal_fields: list[str], cal_row: list[str]) -> tuple[float, int]:
    """Trust the working-time blob over the declared day_hr_cnt."""
    row = dict(zip(cal_fields, cal_row))
    blob = row.get("clndr_data", "")
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"\(0\|\|([1-7])\(\)", blob)]
    hours, days = 0.0, 0
    for i, (pos, _d) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(blob)
        minutes = 0
        for start, finish in re.findall(r"s\|(\d\d:\d\d)\|f\|(\d\d:\d\d)", blob[pos:end]):
            sh, sm = map(int, start.split(":"))
            fh, fm = map(int, finish.split(":"))
            minutes += (fh * 60 + fm) - (sh * 60 + sm)
        if minutes > 0:
            days += 1
            hours = max(hours, minutes / 60)
    if hours == 0:
        hours = float(row.get("day_hr_cnt") or 8)
        days = max(1, int(round(float(row.get("week_hr_cnt") or 40) / hours)))
    return hours, days


# --------------------------------------------------------------------------- pack


def load_pack(root: Path) -> dict:
    pack = {}
    for name in SOURCE_FILES:
        path = root / name
        if not path.exists():
            raise SystemExit(f"missing source file: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            pack[name] = list(csv.DictReader(fh))
    return pack


def _num(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------- build


def build(pack_dir: Path, out: Path, project: str | None, data_date: str | None,
          skeleton: Path) -> dict:
    if not skeleton.exists():
        raise SystemExit(f"skeleton not found: {skeleton}")
    header, tables = read_xer(skeleton)
    pack = load_pack(pack_dir)

    activities = sorted(pack["activities.csv"], key=lambda r: r["activity_id"].strip())
    wbs_nodes = sorted(pack["wbs.csv"], key=lambda r: (int(r.get("level") or 1), r["code"].strip()))
    rels = sorted(pack["relationships.csv"],
                  key=lambda r: (r["predecessor"].strip(), r["successor"].strip()))

    cal_fields, cal_rows = tables["CALENDAR"]
    hpd, working_days = calendar_hours(cal_fields, cal_rows[0])
    if abs(hpd - float(dict(zip(cal_fields, cal_rows[0])).get("day_hr_cnt") or hpd)) > 1e-6:
        print(f"note: calendar declares {dict(zip(cal_fields, cal_rows[0])).get('day_hr_cnt')}h/day "
              f"but working time is {hpd:g}h/day; using {hpd:g}.", file=sys.stderr)

    short_name = project or (wbs_nodes[0]["code"].strip() if wbs_nodes else "PROJECT")
    starts = [a["start"].strip() for a in activities]
    finishes = [a["finish"].strip() for a in activities]
    if not starts:
        raise SystemExit("no activities in the pack")
    plan_start, plan_finish = min(starts), max(finishes)
    data_date = data_date or plan_start

    # ---- PROJECT
    proj_fields, proj_rows = tables["PROJECT"]
    proj = dict(zip(proj_fields, proj_rows[0]))
    proj.update({
        "proj_id": "1",
        "proj_short_name": short_name,
        "clndr_id": "1",
        "plan_start_date": f"{plan_start} 08:00",
        "plan_end_date": f"{plan_finish} 17:00",
        "scd_end_date": f"{plan_finish} 17:00",
        "last_recalc_date": f"{data_date} 08:00",
        "cur_data_date": f"{data_date} 08:00",
        "add_date": f"{data_date} 08:00",
        "export_flag": "Y",
    })
    tables["PROJECT"] = (proj_fields, [[proj.get(f, "") for f in proj_fields]])

    # ---- PROJWBS (skeleton root row is the prototype for non-source fields)
    wbs_fields, wbs_rows = tables["PROJWBS"]
    proto = dict(zip(wbs_fields, wbs_rows[0]))
    wbs_id = {n["code"].strip(): i for i, n in enumerate(wbs_nodes, start=1)}
    new_wbs, seq = [], 0
    for node in wbs_nodes:
        code = node["code"].strip()
        parent = (node.get("parent") or "").strip()
        is_root = not parent
        if not is_root:
            seq += 1
        row = dict(proto)
        row.update(WBS_DEFAULTS)
        row.update({
            "wbs_id": str(wbs_id[code]),
            "wbs_short_name": code,
            "wbs_name": (node.get("name") or code).strip(),
            "parent_wbs_id": str(wbs_id[parent]) if parent in wbs_id else "",
            "proj_node_flag": "Y" if is_root else "N",
            "seq_num": "0" if is_root else str(seq),
        })
        new_wbs.append([row.get(f, "") for f in wbs_fields])
    tables["PROJWBS"] = (wbs_fields, new_wbs)

    # ---- TASK
    task_fields = tables["TASK"][0]
    task_id = {a["activity_id"].strip(): i for i, a in enumerate(activities, start=1)}
    new_tasks = []
    for a in activities:
        days = float(a.get("duration") or 0)
        hours = days * hpd
        row = dict(TASK_DEFAULTS)
        row.update({
            "task_id": str(task_id[a["activity_id"].strip()]),
            "wbs_id": str(wbs_id.get((a.get("wbs") or "").strip(), 0)),
            "task_code": a["activity_id"].strip(),
            "task_name": (a.get("name") or "").strip(),
            "task_type": "TT_Mile" if days == 0 else "TT_Task",
            "target_drtn_hr_cnt": _num(hours),
            "remain_drtn_hr_cnt": _num(hours),
            "target_start_date": f"{a['start'].strip()} 08:00",
            "target_end_date": f"{a['finish'].strip()} 17:00",
        })
        new_tasks.append([row.get(f, "") for f in task_fields])
    tables["TASK"] = (task_fields, new_tasks)

    # ---- TASKPRED
    pred_fields = tables["TASKPRED"][0]
    new_preds = []
    for i, rel in enumerate(rels, start=1):
        pred = rel["predecessor"].strip()
        succ = rel["successor"].strip()
        row = dict(PRED_DEFAULTS)
        row.update({
            "task_pred_id": str(i),
            "task_id": str(task_id.get(succ, 0)),
            "pred_task_id": str(task_id.get(pred, 0)),
            "pred_type": REL_TYPES.get((rel.get("type") or "FS").strip().upper(), "PR_FS"),
            "lag_hr_cnt": _num(float(rel.get("lag") or 0) * hpd),
        })
        new_preds.append([row.get(f, "") for f in pred_fields])
    tables["TASKPRED"] = (pred_fields, new_preds)

    data = write_xer(out, header, tables, ["PROJECT", "CALENDAR", "OBS", "PROJWBS", "TASK", "TASKPRED"])

    filled = set(TASK_DEFAULTS) | {"task_id", "wbs_id", "task_code", "task_name", "task_type",
                                   "target_drtn_hr_cnt", "remain_drtn_hr_cnt",
                                   "target_start_date", "target_end_date"}
    return {
        "output": str(out),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "encoding": "cp1252",
        "project": short_name,
        "data_date": data_date,
        "hours_per_day": hpd,
        "working_days": working_days,
        "counts": {"activities": len(new_tasks), "relationships": len(new_preds),
                   "wbs": len(new_wbs)},
        "task_fields_unset": [f for f in task_fields if f not in filled],
    }


# --------------------------------------------------------------------------- check


def check(xer: Path, pack_dir: Path) -> dict:
    header, tables = read_xer(xer)
    pack = load_pack(pack_dir)
    results = []

    def add(code, ok, detail=""):
        results.append({"code": code, "ok": bool(ok), "detail": detail})

    add("S01", header.startswith("ERMHDR") and len(header.split("\t")) == 9,
        f"{len(header.split(chr(9)))} header fields (expected 9)")

    required = ["PROJECT", "CALENDAR", "OBS", "PROJWBS", "TASK", "TASKPRED"]
    add("S02", all(t in tables and tables[t][1] for t in required),
        ", ".join(t for t in required if t not in tables) or "all present")

    ragged = [t for t, (f, rows) in tables.items() if any(len(r) != len(f) for r in rows)]
    add("S03", not ragged, f"ragged: {ragged}")

    wf, wrows = tables["PROJWBS"]
    wbs = [dict(zip(wf, r)) for r in wrows]
    ids = [w["wbs_id"] for w in wbs]
    roots = [w for w in wbs if w.get("proj_node_flag") == "Y"]
    add("S04", len(set(ids)) == len(ids) and len(roots) == 1,
        f"{len(ids)} nodes, {len(roots)} root(s)")

    tf, trows = tables["TASK"]
    tasks = [dict(zip(tf, r)) for r in trows]
    codes = [t["task_code"] for t in tasks]
    add("S05", len(set(codes)) == len(codes), f"{len(codes)} tasks")
    add("S06", all(t["wbs_id"] in ids for t in tasks),
        f"{sum(1 for t in tasks if t['wbs_id'] not in ids)} orphan(s)")

    pf, prows = tables["TASKPRED"]
    preds = [dict(zip(pf, r)) for r in prows]
    known = set(codes)
    code_of = {t["task_id"]: t["task_code"] for t in tasks}
    bad = [p for p in preds
           if code_of.get(p["pred_task_id"]) not in known or code_of.get(p["task_id"]) not in known]
    add("S07", not bad, f"{len(preds)} links, {len(bad)} unresolvable")

    cal_f, cal_r = tables["CALENDAR"]
    hpd, days = calendar_hours(cal_f, cal_r[0])
    add("S08", hpd > 0, f"{hpd:g}h/day over {days} days")

    mismatched = [t["task_code"] for t in tasks
                  if abs(float(t["target_drtn_hr_cnt"] or 0)
                         - float(next(a["duration"] for a in pack["activities.csv"]
                                      if a["activity_id"].strip() == t["task_code"])) * hpd) > 1e-6]
    add("S09", not mismatched, f"{len(mismatched)} duration mismatch(es)")

    # equivalence with the pack
    csv_codes = {a["activity_id"].strip() for a in pack["activities.csv"]}
    add("E01", csv_codes == known, f"{len(csv_codes)} csv / {len(known)} xer")

    name_bad = [t["task_code"] for t in tasks
                if t["task_name"] != next(a["name"].strip() for a in pack["activities.csv"]
                                          if a["activity_id"].strip() == t["task_code"])]
    add("E02", not name_bad, f"{len(name_bad)} name mismatch(es)")

    csv_links = {(r["predecessor"].strip(), r["successor"].strip())
                 for r in pack["relationships.csv"]}
    xer_links = {(code_of.get(p["pred_task_id"]), code_of.get(p["task_id"])) for p in preds}
    add("E03", csv_links == xer_links,
        f"{len(csv_links - xer_links)} missing, {len(xer_links - csv_links)} extra")

    passed = sum(1 for r in results if r["ok"])
    return {"xer": str(xer), "checks": results,
            "summary": {"total": len(results), "passed": passed,
                        "failed": len(results) - passed,
                        "verdict": "pass" if passed == len(results) else "fail"}}


# --------------------------------------------------------------------------- actions


def actions_xml(xer: Path, out: Path, eps: str) -> Path:
    body = (
        "<actions>\n  <action>\n"
        "    <type>import</type>\n    <importFormat>XER</importFormat>\n"
        "    <importType>PROJECT</importType>\n    <importAction>CREATE</importAction>\n"
        f"    <importTo>{eps}</importTo>\n    <importConfiguration></importConfiguration>\n"
        f"    <importFile>{xer.resolve()}</importFile>\n"
        "  </action>\n</actions>\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


# --------------------------------------------------------------------------- verify


def verify(db: Path, project: str, pack_dir: Path) -> dict:
    pack = load_pack(pack_dir)
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT PROJ_ID FROM PROJECT WHERE PROJ_SHORT_NAME=?",
                            (project,)).fetchall()
        if len(rows) != 1:
            return {"accepted": False, "problems": [f"expected 1 project '{project}', found {len(rows)}"]}
        pid = rows[0]["PROJ_ID"]
        tasks = conn.execute("SELECT TASK_CODE, TASK_NAME FROM TASK WHERE PROJ_ID=?", (pid,)).fetchall()
        wbs = conn.execute("SELECT COUNT(*) c FROM PROJWBS WHERE PROJ_ID=?", (pid,)).fetchone()["c"]
        links = conn.execute("SELECT COUNT(*) c FROM TASKPRED WHERE PROJ_ID=?", (pid,)).fetchone()["c"]

        expected = {a["activity_id"].strip(): a for a in pack["activities.csv"]}
        stored = {t["TASK_CODE"]: t["TASK_NAME"] for t in tasks}
        problems = []
        if set(stored) != set(expected):
            missing = sorted(set(expected) - set(stored))
            extra = sorted(set(stored) - set(expected))
            problems.append(f"activity set differs: {len(missing)} missing, {len(extra)} extra "
                            f"({(missing + extra)[:5]})")
        if wbs != len(pack["wbs.csv"]):
            problems.append(f"WBS nodes {wbs} vs {len(pack['wbs.csv'])}")
        if links != len(pack["relationships.csv"]):
            problems.append(f"links {links} vs {len(pack['relationships.csv'])}")
        return {
            "accepted": not problems,
            "project": project,
            "project_id": pid,
            "counts": {"activities": len(tasks), "wbs": wbs, "relationships": links},
            "problems": problems,
        }
    finally:
        conn.close()


# --------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="p6native", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build an XER from CSV sources")
    b.add_argument("--pack", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--project", default=None)
    b.add_argument("--data-date", default=None)
    b.add_argument("--skeleton", default=str(SKELETON))
    b.add_argument("--report", default=None)

    c = sub.add_parser("check", help="check an XER against its CSV sources")
    c.add_argument("--xer", required=True)
    c.add_argument("--pack", required=True)
    c.add_argument("--json", action="store_true")

    a = sub.add_parser("actions", help="write a P6 CLI import action script")
    a.add_argument("--xer", required=True)
    a.add_argument("--out", default="actions.xml")
    a.add_argument("--eps", default="")

    v = sub.add_parser("verify", help="read a stored project back from a P6 SQLite database")
    v.add_argument("--db", required=True)
    v.add_argument("--project", required=True)
    v.add_argument("--pack", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "build":
        report = build(Path(args.pack), Path(args.out), args.project, args.data_date,
                       Path(args.skeleton))
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0

    if args.cmd == "check":
        result = check(Path(args.xer), Path(args.pack))
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for r in result["checks"]:
                print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['code']}  {r['detail']}")
            s = result["summary"]
            print(f"\n  {s['passed']}/{s['total']} -> {s['verdict'].upper()}")
        return 0 if result["summary"]["verdict"] == "pass" else 1

    if args.cmd == "actions":
        path = actions_xml(Path(args.xer), Path(args.out), args.eps)
        print(f"wrote {path}")
        return 0

    if args.cmd == "verify":
        result = verify(Path(args.db), args.project, Path(args.pack))
        print(json.dumps(result, indent=2))
        return 0 if result["accepted"] else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
