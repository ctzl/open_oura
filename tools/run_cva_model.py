#!/usr/bin/env python3
"""Estimate cardiovascular age + pulse-wave velocity from the ring's raw PPG.

Decodes the `cva_raw_ppg_data` events (BLE tag 0x81) the ring emits when the
`cva_ppg` (CAP_CVA_PPG_SAMPLER, id 13) feature is enabled, reconstructs the PPG
waveform, segments it the way the app does (groups of 1500 samples), and runs
Oura's decrypted `cva_1_3_0.pt` model (the raw-PPG pipeline).

Decode: each 0x81 body is a stateful delta stream. Byte 0x80 marks the next
three bytes as a signed 24-bit absolute ADC sample; every other byte is a signed
int8 delta from the previous sample. A measurement = a run of events <2 s apart
(~1500 samples ≈ 30 s @ 50 Hz). The model wants segments of exactly 1500 samples.

Model I/O (from the TorchScript source inside cva_1_3_0.pt):
  forward(ppg_segments [n,1500] f32, sampling_rates [n,1] f32=50,
          timestamps [n,1] f32, demographics [1,4] f32)
    demographics = [sex(-1 F / +1 M / 0 other), height_m, age_years, ring_size]
  -> (daily_cva, quality, variance, daily_pwv, ppg_segment_metrics[n,11])

The ring streams this PPG at 50 Hz (measured ~50.0-50.3 Hz across runs); the
model hard-requires sampling_rates == 50. Timestamps are only shape-validated —
they do not affect the output.

NOTE: daily_cva tracks the input age (it is a *vascular* age anchored to your
chronological age and adjusted by PPG morphology), so pass real demographics for a
meaningful number. daily_pwv (m/s) is PPG-only.

(The newer cva_2_1_5.pt in the APK is a "cva-from-emb" variant that takes
128-dim embeddings, not raw PPG — it needs the upstream embedding model we don't
have, so this runner uses the raw-PPG v1 pipeline instead.)

Usage: python tools/run_cva_model.py [DB] [--sex M|F|O] [--age Y] [--height M]
                                      [--ring N] [--since-cursor DS]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import torch

from _common import resolve_db

REPO = Path(__file__).resolve().parent.parent
SEG_LEN = 1500          # samples per segment (hard model constant)
GAP_DS = 20             # >2 s (deciseconds) splits two PPG measurements


def find_model():
    """Newest decrypted raw-PPG cva model in notes/models (cva_1_*, not the
    from-emb cva_2_* which needs 128-dim embeddings we can't produce)."""
    cands = sorted((REPO / "notes" / "models").glob("cva_1_*.pt"))
    if not cands:
        sys.exit("no decrypted cva_1_*.pt in notes/models — decrypt with model_key.hex first")
    return cands[-1]


def decode_cva_ppg_bodies(bodies):
    samples = []
    acc = 0
    for body in bodies:
        data = bytes(body)
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0x80 and i + 3 < len(data):
                raw = data[i + 1] | (data[i + 2] << 8) | (data[i + 3] << 16)
                if raw & 0x800000:
                    raw -= 0x1000000
                acc = raw
                samples.append(acc)
                i += 4
            else:
                acc += (b - 256 if b > 127 else b)
                samples.append(acc)
                i += 1
    return np.asarray(samples, dtype=np.float32)


def build_segments(db, since_ds):
    con = sqlite3.connect(str(db))
    rows = con.execute(
        "SELECT ring_timestamp, body FROM events WHERE tag=129 AND ring_timestamp>? "
        "ORDER BY ring_timestamp",
        (since_ds,),
    ).fetchall()
    con.close()
    if not rows:
        sys.exit("no cva_raw_ppg_data (tag 0x81) events — is cva_ppg enabled? (oura feature-status)")
    ts = np.array([r[0] for r in rows])
    # split into contiguous measurement runs, decode each, chunk into 1500
    runs, cur = [], [0]
    for i in range(1, len(rows)):
        if ts[i] - ts[i - 1] > GAP_DS:
            runs.append(cur)
            cur = []
        cur.append(i)
    runs.append(cur)
    segs = []
    seg_ts = []
    for run in runs:
        wave = decode_cva_ppg_bodies([rows[k][1] for k in run])
        for s in range(0, len(wave) - SEG_LEN + 1, SEG_LEN):
            segs.append(wave[s : s + SEG_LEN])
            seg_ts.append(ts[run[0]] + s)  # segment start, ring deciseconds
    return segs, seg_ts, len(runs)


def load_profile(db):
    # profile.json (editable, gitignored) sits next to the DB; CLI args override it.
    import json
    try:
        return json.loads((Path(db).parent / "profile.json").read_text())
    except Exception:
        return {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("db", nargs="?", default=None)
    # default None → fall back to profile.json, then to a built-in default
    p.add_argument("--sex", default=None, choices=["M", "F", "O"])
    p.add_argument("--age", type=float, default=None)
    p.add_argument("--height", type=float, default=None, help="meters")
    p.add_argument("--weight", type=float, default=None, help="kg")
    p.add_argument("--ring", type=float, default=None, help="ring size")
    p.add_argument("--since-cursor", type=int, default=0, help="only events with ring_timestamp > this")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = p.parse_args()
    model_path = find_model()

    db = resolve_db(args.db, REPO)
    prof = load_profile(db)
    pick = lambda cli, key, dflt: cli if cli is not None else prof.get(key, dflt)
    args.sex = pick(args.sex, "sex", "M")
    args.age = float(pick(args.age, "age", 30.0))
    args.height = float(pick(args.height, "height_m", 1.78))
    args.weight = float(pick(args.weight, "weight_kg", 75.0))
    args.ring = float(pick(args.ring, "ring_size", 10.0))

    segs, seg_ts, n_runs = build_segments(db, args.since_cursor)
    if not segs:
        sys.exit(f"no full {SEG_LEN}-sample PPG segment available ({n_runs} measurement runs, all too short)")
    ppg = torch.tensor(np.stack(segs), dtype=torch.float32)
    rates = torch.full((ppg.shape[0], 1), 50.0, dtype=torch.float32)  # ring streams at 50 Hz
    tst = torch.tensor([[float(t)] for t in seg_ts], dtype=torch.float32)
    sex = {"F": -1.0, "M": 1.0, "O": 0.0}[args.sex]
    demo = torch.tensor([[sex, args.height, args.age, args.ring]], dtype=torch.float32)

    m = torch.jit.load(str(model_path), map_location="cpu").eval()
    with torch.no_grad():
        cva, quality, variance, pwv, seg_metrics = m(ppg, rates, tst, demo)

    if args.json:
        import json
        print(json.dumps({
            "segments": int(ppg.shape[0]), "measurements": n_runs,
            "vascular_age": round(cva.item(), 1), "chronological_age": args.age,
            "pwv_ms": round(pwv.item(), 2),
            "quality": round(quality.item()), "variance": round(variance.item(), 2),
        }))
        return

    print(f"CVA (cardiovascular age) — {ppg.shape[0]} PPG segments from {n_runs} measurements")
    print(f"  demographics: sex={args.sex} age={args.age:.0f} height={args.height} ring={args.ring}")
    print(f"  vascular age : {cva.item():.1f} years   (anchored to input age; here {cva.item()-args.age:+.1f}y vs chronological)")
    print(f"  pulse-wave velocity: {pwv.item():.2f} m/s   (lower = less arterial stiffness)")
    print(f"  quality: {quality.item():.0f}  variance: {variance.item():.2f}")


if __name__ == "__main__":
    main()
