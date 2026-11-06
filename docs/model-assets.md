# Oura model assets: fetch, decrypt, inventory

How we got the on-device ML models out of the Oura app, decrypted them, and
what each one is. Verified against Oura app **5.5.0** (XAPK manifest 7.15.0,
version code 260521094) on a **Pixel 9 Pro XL running LineageOS**, Nov 2026.
Companion: `docs/algorithms/sleepnet.md` (key-capture detail),
`docs/cva-cardiovascular-age.md` (CVA deep-dive).

## 1. Where the models live

The app ships its TorchScript models as `assets/*.pt.enc` inside a **separate
split APK** (`split_oura_models.apk`, ~58 MB, 33 assets) installed alongside
the base app — not in `base.apk`, not downloaded at runtime. Split APKs live
in the app's install dir next to `base.apk`; we pulled it off the phone with
root adbd (LineageOS):

```
adb pull "/data/app/~~GA5s63-YaVV9e8ufM42O5A==/com.ouraring.oura-eheVKh7CyiR4z3OUKrQhQg==/split_oura_models.apk" \
    tools/captures/android/split_oura_models.apk
```

(The `~~<hash>`/`-<hash>` dir components change on every reinstall — find the
current one with `ls /data/app | grep oura`.) Saved to
`tools/captures/android/split_oura_models.apk` (gitignored; alternatively
extract it from the XAPK without a phone at all).

## 2. Encryption

Each `*.pt.enc` is **AES-256-GCM**, framed as `iv(12) || ciphertext || tag(16)`.
Plaintext is a TorchScript archive, so decrypted blobs start with zip magic
`PK\x03\x04`.

**The key is server-delivered** — it is *not* in the APK, not derived from
anything in the APK, and never stored on the phone in plaintext (the app keeps
it in memory after login). One single key decrypts **all 33** assets. It was
captured from the running app with Frida (`tools/capture_model_key.py` +
`tools/frida/cipher_key_hook.js`, hooking the GCM cipher init; procedure and
pitfalls in `docs/algorithms/sleepnet.md`). Saved as 64 hex chars in
`model_key.hex` at the repo root — **gitignored**; losing it means re-capturing
(Frida must be re-pushed to the phone: we removed `frida-server` from
`/data/local/tmp` after the capture, so re-capture = push frida-server, run as
root, attach to the Oura process right after a fresh login).

## 3. Decrypting

```
python tools/decrypt_models.py            # all 33 -> notes/models/*.pt (gitignored)
python tools/decrypt_models.py --list     # verify key without writing
python tools/decrypt_models.py --only cva # subset
```

Reads `model_key.hex`, prints `PK` magic check per asset. As of this writing:
**33/33 decrypt OK**. `notes/models/` is gitignored (server-licensed binaries).

## 4. Inventory (33 assets, app 5.5.0)

Schemas are the real `forward()` signatures dumped from the TorchScript
archives (saved in `notes/model_schemas.json`). "Runnable" = we have a working
runner in this repo or open_health.

| Asset | TorchScript class | What it is | Status |
|---|---|---|---|
| `astd_event_detection_0_1_1` | `astd_event_detection.EventDetection` | event detection (2 in, 8 out) | **RUNNING** — `tools/run_stress_events.py` |
| `atlas_3_0_0` | `BioZ` | bioimpedance (atlas) sensing, 6 in / 12 out | blocked: ring stream `0x88 atlas_raw_bioz_data` is backend-gated |
| `atlas_trendline_1_1_0` | `TrendlineCombo` | bioz trendlines, 5 in / 15 out | blocked (same gate) |
| `automatic_activity_detection_3_1_12` / `_3_1_15` | `AutomaticActivityDetectionModel` | AAD: movement/activity classes from IMU (12–14 in) | `tools/run_activity_model.py` exists but is pinned to `3_1_11`, gone in 5.5.0 — needs re-pinning |
| `awhr_imputation_1_2_0` | `WorkoutHeartRateImputationModel` | fills gaps in workout HR | not run |
| `awhr_profile_selector_1_0_1` | `AwhrProfileSelectorModel` | picks per-user HR profile | not run |
| `cumulative_stress_2_0_0` | `ChronicStress` | long-horizon stress (28 in / 19 out) | inputs overlap our stress pipeline; not run |
| `cva_1_3_0` | `algo_cva.main.MainAlgo` | **cardiovascular age from raw PPG** — `forward(ppg, rates, timestamps, demo[4])` | **RUNNING** — `tools/run_cva_model.py` here + open_health dashboard |
| `cva_2_1_5` | `algo_cva.main.MainAlgo` | CVA variant taking **embeddings** (2 args) | blocked: needs the embedding model that produces its 128-dim input |
| `cva_calibrator_1_3_0` | `algo_cva.calibrator.Calibrator` | calibrates CVA per user (9 in / 6 out) | not run |
| `daily_medians_1_1_0` | `StressMedians` | daily stress medians | not run |
| `daily_short_term_baselines_1_1_0` | `StressBaselines` | short-term baselines | not run |
| `dhrv_imputation_1_1_0` | `DhrvImputer` | fills missing HRV | not run |
| `energy_expenditure_2_1_1` / `_2_4_1` | `EnergyExpenditureModel` | calories (16 in) | inputs = IMU+HR; not run |
| `halite_1_2_0` | `halite.inference.halite_combo.Halite` | codename unknown; first arg is optional Tensor + int | unexplored |
| `high_low_hr_1_1_0` | `HiloHRv1_1Module` | daily high/low HR markers | not run |
| `illness_detection_0_5_1` | `IllnessDetectionModel` | illness detection (23 in / 18 out) | not run |
| `locomotion_0_0_10` | `LocomotionModel` | gait/locomotion (25 in / 15 out) | not run |
| `meal_timing_0_1_0` | `MealWindowClustering` | clusters eating windows (3 in) | not run |
| `popsicle_1_6_0` / `_1_9_1` | `popsicle.popsicle_combo.Popsicle` | codename unknown (12–13 in / 9–12 out) | unexplored |
| `pregnancy_biometrics_0_4_0` | `PregnancyBiometrics` | pregnancy features (7 in / 21 out) | not run |
| `sleepnet_bdi_0_4_0` | `SleepNetModel` | sleep staging, 3-arg variant | not run |
| `sleepnet_moonstone_1_2_0` | `SleepNetModel` | **sleep staging, 11-arg variant** | **RUNNING** — `tools/run_sleep_model.py` |
| `sleepstaging_2_6_0` | — | sleep staging | **won't load**: calls custom op `oura_ops::oura_create_windows` from its internal `hrv_raw_to_low_level_features` module (needs Oura's custom TorchScript ops registered) |
| `step_counter_1_3_0` | `AppStepCounterModel` | steps from IMU | not run |
| `steps_motion_decoder_2_0_0` | `StepsMotionDecoderModel` | decodes step motion type | helper input for `run_activity_model.py` |
| `stress_daytime_sensing_1_1_0` | `DaytimeStressSensing` | daytime stress events | **RUNNING** — `tools/run_stress_model.py` / `run_stress_events.py` |
| `stress_resilience_2_2_2` | `Resilience` | resilience score (15 in) | not run |
| `training_stress_score_0_2_2` | `stress_ots.OTS` | training stress | not run |
| `whr_2_7_1` | `ExerciseHR...UNetCombinedModule` | exercise HR (4.2 MB, largest asset) | not run |

## 5. The set rotates per app version

`tools/export_mobile.py` / `tools/inspect_models.py` reference an **older**
app's set that no longer exists in 2.55.1: `cva_2_1_0` (replaced by
`cva_1_3_0` + `cva_2_1_5`), `insomnia_0_1_4` (gone), `atlas_2_1_0` →
`atlas_3_0_0`, `energy_expenditure_1_0_0` → `2_1_1`/`2_4_1`,
`stress_resilience_2_2_1` → `2_2_2`, `automatic_activity_detection_3_1_11` →
`3_1_12`/`3_1_15`. Lesson: **never pin a model filename** — resolve newest
matching prefix (what `find_model()` in the CVA runners does). Two codenames
(`halite`, `popsicle`) have no user-facing feature we can map yet; `atlas` =
bioimpedance, `whr` = workout/exercise HR.

## 6. Gotchas

- `tools/run_activity_model.py` is currently broken against 5.5.0 assets: it
  pins `automatic_activity_detection_3_1_11.pt`, which this app version no
  longer ships (it has `3_1_12`/`3_1_15`). Same failure mode as the CVA
  runner's `cva_2_1_0` pin — resolve newest-prefix instead.
- The key is tied to the account/server session; if a future app update
  changes it, `--list` failing = re-capture, not a code bug.
- `sleepstaging_2_6_0` needs `oura_ops::oura_create_windows` — vanilla
  `torch.jit.load` refuses it; would need the custom op implemented.
- `cva_2_1_5` consumes embeddings, not raw PPG — don't feed it PPG windows.
- Windows: run tools with native `C:/`-style paths; `notes/models/` and
  `model_key.hex` are gitignored — back them up outside the repo.
