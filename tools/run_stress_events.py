#!/usr/bin/env python3
"""Detect Oura's *stress / restored events* locally by chaining two decrypted
models — no cloud:

    stress_daytime_sensing_1_1_0   -> per-sample intensity_scaled  (the "DSA" series)
    astd_event_detection_0_1_1     -> stress/rest events from that series

`astd_event_detection` ("Automatic Stress & rest event Detection") takes
`dsa_values` (the scaled stress values, [-1, 1]) and `dsa_timestamps_ms`
(strictly increasing). Its internals (recovered from the TorchScript):

    classify:  value <= -0.5 fully stressed | -0.5 < v <= -0.4 borderline stressed
               value >= +0.5 fully restored | +0.4 <= v < +0.5 borderline restored
    segment:   an event = N=4 consecutive qualifying 15-min bins inside a
               65-min window (min_window_delta 3.3e6 .. max 3.9e6 ms);
               same-type events closer than 30 min merge.
    returns:   (n_stressed, n_restored, total_stressed_min, total_restored_min,
                event_type_ids, event_start_ms, event_end_ms, durations_min)

Two quirks of the shipped model (0.1.1) we work around here:
  * `dsa_timestamps_ms` is cast to **int32** inside `segment`, so timestamps
    must be day-relative (absolute ring ms overflow int32 after ~25 days).
    We run one window per calendar day.
  * when a day has **zero** events, `forward` indexes an empty tensor and
    raises "index 0 is out of bounds" — that is a model-side bug, not bad
    input; we treat it as "no events that day".

Usage: python tools/run_stress_events.py [DB] [--tz N] [--json]
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from _common import resolve_db  # noqa: E402
from epoch_time import build_epochs, make_unix_s  # noqa: E402

MODEL = REPO / "notes" / "models" / "astd_event_detection_0_1_1.pt"
DAY_MS = 86_400_000
MIN_BINS = 4  # the model's own validator minimum


def stress_series(db):
    """Run the daytime-stress model and return [(ts_ms, scaled_value)]."""
    out = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run_stress_model.py"), str(db), "--json"],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(out.stderr.strip() or out.stdout.strip())
    data = json.loads(out.stdout)
    # ring deciseconds -> ms; keep the latest value per timestamp (strictly
    # increasing input is required by the validator)
    dedup = {}
    for s in data["samples"]:
        dedup[s["timestamp_ds"] * 100] = s["intensity_scaled"]
    return sorted(dedup.items()), data


def main():
    ap = argparse.ArgumentParser(description="Local stress/rest event detection (DSA chain)")
    ap.add_argument("db", nargs="?", help="events DB (default: ./oura.db)")
    ap.add_argument("--tz", type=int, default=1, help="hours offset for printed clock times")
    ap.add_argument("--json", action="store_true", help="emit events as JSON")
    args = ap.parse_args()

    if not MODEL.exists():
        sys.exit(f"error: model not found: {MODEL} (decrypt notes/models first)")
    model = torch.jit.load(str(MODEL), map_location="cpu").eval()

    db = resolve_db(args.db, REPO)
    series, stress_meta = stress_series(db)
    if not series:
        sys.exit("no daytime-stress samples — enable `oura feature daytime_hr` and sync?")

    # date the ring timestamps (same epoch machinery as the other runners)
    import sqlite3
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT ring_timestamp, tag, decoded_json, captured_unix FROM events "
        "WHERE captured_unix IS NOT NULL ORDER BY captured_unix, id").fetchall()
    con.close()
    unix_s = make_unix_s(build_epochs(rows))

    days = {}
    for ts_ms, v in series:
        u = unix_s(int(ts_ms // 100))
        if u is None:
            continue
        days.setdefault(int(u) // 86400, []).append((ts_ms, v))

    results = []
    for day, pts in sorted(days.items()):
        if len(pts) < MIN_BINS:
            continue
        base = pts[0][0]
        ts = torch.tensor([t - base for t, _ in pts], dtype=torch.float32)
        vals = torch.tensor([v for _, v in pts], dtype=torch.float32)
        try:
            r = model(vals, ts)
        except Exception as e:
            msg = str(e).strip().splitlines()[-1]
            if "out of bounds" in msg:
                r = None  # zero events (model-side indexing bug)
            else:
                results.append({"day": day, "error": msg})
                continue
        if r is None:
            results.append({"day": day, "n_samples": len(pts), "events": []})
            continue
        n_s, n_r, tot_s, tot_r, types, starts, ends, durs = (x.flatten().tolist() for x in r)
        events = []
        for i in range(len(types)):
            start_ms = base + starts[i]
            end_ms = base + ends[i]
            u0 = unix_s(int(start_ms // 100))
            u1 = unix_s(int(end_ms // 100))
            events.append({
                "type": "stressed" if types[i] == -1 else "restored",
                "start_local": _fmt(u0, args.tz), "end_local": _fmt(u1, args.tz),
                "duration_min": round(durs[i], 1),
            })
        results.append({
            "day": day, "n_samples": len(pts),
            "n_stressed": int(n_s[0]) if hasattr(n_s, "__len__") else int(n_s),
            "n_restored": int(n_r[0]) if hasattr(n_r, "__len__") else int(n_r),
            "total_stressed_min": round(float(tot_s[0] if hasattr(tot_s, "__len__") else tot_s), 1),
            "total_restored_min": round(float(tot_r[0] if hasattr(tot_r, "__len__") else tot_r), 1),
            "events": events,
        })

    if args.json:
        print(json.dumps({"stress_baseline": {k: stress_meta[k] for k in
                          ("night_hrv_baseline", "dhrv_baseline")}, "days": results}, indent=2))
        return

    print(f"Stress/rest events (astd_event_detection_0_1_1 on local DSA series)")
    print(f"  inputs: {len(series)} daytime-stress samples over {len(days)} day(s)")
    for res in results:
        day = res["day"]
        date = (datetime(1970, 1, 1) + timedelta(milliseconds=day * DAY_MS)).date()
        if "error" in res:
            print(f"  {date}: model error: {res['error']}")
            continue
        ev = res.get("events", [])
        print(f"  {date}: {res['n_samples']} samples | stressed {res.get('n_stressed', 0)} "
              f"({res.get('total_stressed_min', 0)} min) | restored {res.get('n_restored', 0)} "
              f"({res.get('total_restored_min', 0)} min)")
        for e in ev:
            print(f"      [{e['type']:8s}] {e['start_local']} -> {e['end_local']}  {e['duration_min']} min")


def _fmt(unix, tz):
    if unix is None:
        return "undated"
    return (datetime(1970, 1, 1) + timedelta(seconds=unix + tz * 3600)).strftime("%m-%d %H:%M")


if __name__ == "__main__":
    main()
