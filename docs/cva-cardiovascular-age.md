# Cardiovascular age from raw PPG (`cva_ppg` → `cva_raw_ppg_data` → CVA model)

> **Models are not included in this repo.** The decrypted `cva_1_3_0.pt` (and every
> other `.pt`) is Oura's proprietary IP; it is **not committed** (gitignored under
> `notes/models/`). Decrypt them from `split_oura_models.apk` with the model key
> (see `docs/algorithms/sleepnet.md` → "Resolved"). Run this against your own
> locally-decrypted copy.

When the **`cva_ppg`** feature (`CAP_CVA_PPG_SAMPLER`, id `0x0d`) is enabled, the
ring records short raw-PPG bursts overnight and emits them as **`cva_raw_ppg_data`**
events (BLE tag **`0x81`**). This is the raw photoplethysmography waveform the
cardiovascular-age (CVA) model needs — the one signal the PPG-gated models used to
be blocked on. With it captured, we can run Oura's own CVA model offline.

## Decoding tag `0x81`

Each event body is a stateful delta stream. Byte `0x80` marks the next three bytes
as a signed 24-bit **absolute** ADC sample; every other byte is a signed int8 delta
from the previous sample. The Rust decoder (`oura-protocol`,
`decode_cva_raw_ppg`) now understands these absolute markers and emits
`{"ppg_samples": [...], "n": N, "absolute_markers": M}` per event.

A full measurement spans a *burst* of consecutive events **<2 s apart** (~1500
samples ≈ 30 s at **50 Hz** — measured directly from `ring_timestamp` deltas across
Ring 4 bursts; the model's own validator hard-requires `sampling_rates == 50`). For
exact waveform
continuity, concatenate the burst while carrying the last absolute value across
adjacent `0x81` records; the generic event-body decoder is intentionally
stateless and restarts from zero for records that do not contain an absolute
marker.

Validation that the decode is correct: the reconstructed waveform is pulsatile and
its **pulse rate matches the independent IBI-derived heart rate** (e.g. 38 bpm from
PPG peaks vs 41 bpm from IBI in the same window).

## The model (`cva_1_3_0`, raw-PPG pipeline)

The current APK (4.66.0) ships two CVA models. **`cva_1_3_0.pt` is the one that
consumes raw PPG** and is what `tools/run_cva_model.py` runs. `cva_2_1_5.pt` is a
`cva-from-emb` variant that takes **128-dim embeddings** from an upstream embedding
model we don't have — feeding it raw PPG fails its validator (error 112).

```
forward(ppg_segments [n, 1500] f32, sampling_rates [n, 1] f32,
        timestamps [n, 1] f32, demographics [1, 4] f32)
  -> (daily_cva, quality, variance, daily_pwv, ppg_segment_metrics[n, 11])
```

- **`ppg_segments`**: groups of **exactly 1500** raw samples (the app drops any
  group that isn't 1500). No filtering/normalization in the app — preprocessing is
  inside the `.pt`. Feed raw reconstructed samples.
- **`sampling_rates`**: must be **50** (the validator rejects anything else).
- **`timestamps`**: shape-validated only — verified that deciseconds/seconds/ms all
  produce identical output.
- **`demographics` = `[sex, height_m, age_years, ring_size]`**, where
  **sex = −1 female / +1 male / 0 other**. These come from the user profile, not
  the ring (the ring's `user_information` event has no anthropometrics).

### Outputs
- **`daily_cva`** = **vascular age (years)**. It is *anchored to the input age* and
  adjusted by PPG morphology. **Pass real demographics for a meaningful absolute
  number.**
- **`daily_pwv`** = **pulse-wave velocity (m/s)**, PPG-only (lower = less arterial
  stiffness).
- `quality` / `variance` = signal-quality scores; `ppg_segment_metrics` = 11
  per-segment features.

## Running it

```
python tools/run_cva_model.py [DB] --sex M --age 42 --height 1.74 --ring 11
```

Demographics default to `profile.json` (written by `oura-cli sync` from the ring's
`user_information` event). On one night of Ring 4 data (121 valid 1500-sample
segments from 153 measurements): vascular age ≈ 40.4 y vs 42 chronological
(−1.6 y), PWV ≈ 6.4 m/s, quality 2.

## Caveats
- Absolute `daily_cva` is only meaningful with the user's real demographics.
- We segment by a 2 s event-gap heuristic; the app groups by the protobuf
  `measurementStartTimestamp` (decoded by the native lib we don't run), so a borderline
  burst could be split differently. The model still produces a stable value.
- The 50 Hz rate is confirmed by the model's own validator and by measured
  `ring_timestamp` deltas; segments are fed as raw samples (the model bakes in its
  canonical rate). See `tools/run_cva_model.py`.
