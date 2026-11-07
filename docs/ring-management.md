# Ring management — multi-device support (spec)

Status: spec only, not implemented. Written 2026-11-07.
Repos touched: `open_oura` (store + CLI) and `open_health` (summary + dashboard).

## Why

The offline stack currently assumes exactly one ring per `oura.db`. The plan is to
run the (unbanned, better-battery) second ring alongside the banned one, and to be
able to browse either ring's history from the same dashboard.

## Known device identities (from tools/captures/android, verified 2026-11-07)

| role | serial | public MAC | fw | notes |
|---|---|---|---|---|
| banned ring | `40160B2500000002` | `AA:BB:CC:00:00:01` | 2.12.3 | already in `oura.db` (973k events) |
| daily ring | `40160B2500000001` | `AA:BB:CC:00:00:02` | 2.12.13 | `enforcement_status: unlocked`; not yet synced |

Source: `app-live.log` / `appdata-files/app.log` (`Set RingConfiguration` lines),
`client_config.json`, `shared_prefs/sweetblue_qurhzpoc.xml` (BLE names
`Oura <serial>`), `live-assa-store.realm`. The btsnoop log contains the daily
ring's public address as an advertiser. Both rings share `hardware_type: Oreo,
design: OreoOriginal, size 11, Silver` — serial is the only discriminator.

## Requirements (from user)

1. Manage at least 2 rings.
2. Frontend shows data for the **currently selected ring only** — filter DB data
   by the device's unique id (serial).
3. Each ring can be given a **custom name** to tell them apart.

## Design

### 1. Identity = serial (already the de-facto key)

`events.serial`, `readings.serial`, `device.serial`, `sync_state.serial` all exist
and are indexed (`idx_events_serial_tag`). No schema change to event storage.
The gap is that nothing *enumerates* devices and nothing downstream *filters* by
them.

### 2. Registry table (open_oura / oura-store)

```sql
CREATE TABLE IF NOT EXISTS rings (
  serial        TEXT PRIMARY KEY,     -- 16 hex chars, from device/info
  name          TEXT NOT NULL,        -- user-set label
  added_unix    INTEGER NOT NULL,
  last_seen_unix INTEGER,             -- updated on every successful auth/sync
  note          TEXT                  -- optional free text (e.g. "banned")
);
```

- Auto-upsert a row whenever a `device` row is written (pair/sync/info), with
  default name `Ring <last4 of serial>` so nothing is ever anonymous.
- Bump `store_meta.ring_registry_version = 1`; `decoder_version` untouched.

### 3. CLI (open_oura / oura-cli)

- `oura rings` — list registry + per-serial event counts and last sync.
- `oura ring-name <serial> <name>` — set the label (also `--note`).
- Pairing a second ring needs no new code: key files already default to
  `oura-<serial>.key`; both rings live in the same `oura.db` (or separate DBs —
  the registry makes the shared-DB path the supported one).
- Never run two `oura.exe` processes concurrently (unchanged constraint).

### 4. Summary filtering (open_health / oura-summary + oura-core)

- `build_summary(db, tz, serial: Option<&str>)`; `None` = single-device DB
  (auto-detect: if the registry has exactly one serial, use it; if more, require
  the parameter).
- Every event/reading query gains `AND serial = ?` when a serial is given —
  including `clock_rows`/`RingClock::from_events` (each ring has its own clock
  anchors; mixing them is the correctness bug this whole feature prevents),
  night detection, `events_json` (raw browser), `api/live-hr`, and
  `research_export`.
- Summary JSON gains `"serial"` + `"ring_name"` fields (the device row already
  leaks `serial` today — formalize it).

### 5. Model cache keying (open_health / tools)

`nssa_cache.json` and the CVA cache are keyed by night window only. Two rings can
produce the same window (overlap days, or a future backfill), so the cache key
must become `serial|window`. Bump the cache format version; old entries migrate
under the single existing serial.

### 6. Dashboard (open_health / dashboard/web)

- Header: a small device switcher (dropdown) listing
  `<name> · <serial last4> · last sync <when>`, default = most recently synced.
- Selection persists in `localStorage` and is sent as `?serial=` on every
  `/api/*` call; the server validates it against the registry (unknown serial →
  400, not silent empty).
- Rename inline from the switcher (PUT `/api/rings/<serial>` → writes `rings`
  table). Keep the CLI as the fallback.
- With exactly one registered ring the switcher renders as a static label
  (name + rename affordance), not a dropdown.
- Summary cache key in `dashboard.rs` must include the serial.

### 7. Migration / compat

- Existing DBs: first open auto-registers every serial found in `device`/
  `events`; the banned ring gets its default name and the user renames it.
- Old dashers/CLIs that ignore the registry keep working (they just don't
  filter — acceptable until the switcher lands).

## Non-goals (explicitly out)

- No merging/averaging of two rings' data into one night. One ring per view.
- No simultaneous BLE sessions (one `oura.exe` at a time stays true).
- No cross-ring diff/comparison view yet — the switcher is the prerequisite.

## Test plan

- Unit: registry upsert + default naming; `build_summary` with serial filter
  returns byte-identical output to today's single-ring result for the banned
  ring (regression guard).
- Fixture: import a small slice of the daily ring (it has no local data yet —
  first sync will create it) or synthesize a second serial in a test DB.
- Manual: switch rings in the UI, confirm every panel (hypnogram, HR, battery,
  raw browser, live-hr) follows the selection and the model cache doesn't
  cross-contaminate.

## Sequencing

1. `rings` table + auto-register + `oura rings` / `oura ring-name` (open_oura).
2. `serial` param through `build_summary` + all queries + cache keys (open_health).
3. `/api/rings` + switcher + rename UI (open_health dashboard).
4. Sync the daily ring once, verify its panels end to end.
