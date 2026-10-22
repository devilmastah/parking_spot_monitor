# Parking Spot Monitor — developer guide

Home Assistant add-on that photographs one ESP32-CAM per parking bay, decides occupancy from roof color, reads an ArUco marker, matches it to a fleet car, and publishes MQTT discovery entities.

Current add-on version is in `parking_spot_monitor/config.yaml` (`version`, currently 2.6.5). There is no automated test suite.

## Repository layout

```
parking_spot_monitor/          HA add-on (this is what gets built and installed)
  config.yaml                  Add-on manifest: options schema, ingress, architectures
  build.yaml                   Base images per arch (HA Alpine)
  Dockerfile                   Python 3 + Alpine OpenCV
  run.sh                       bashio: options → environment variables, then Python
  requirements.txt             FastAPI, uvicorn, httpx, paho-mqtt, APScheduler, aiosqlite
  src/
    main.py                    Process entry: DB, scheduler, MQTT, HTTP
    config.py                  Settings from environment
    api.py                     HTTP API used by the web UI
    analyzer.py                Sequential capture + per-bay analysis
    ha_client.py               HA camera proxy + ESPHome prepare script
    aruco.py                   Marker detection
    occupancy.py               Red-roof / gray-floor occupancy and dark-frame check
    bay_matching.py            expected car → yes / no / unknown / uncertain
    mqtt_publisher.py          HA MQTT discovery and retained state
    database.py                SQLite (parking.db)
    templates/index.html       Web UI shell
    static/app.js              Web UI behavior
    static/style.css
esphome/                       Firmware for the cameras (not part of the add-on image)
  parking_bay_base.yaml        Shared camera, flash, scripts (do not flash alone)
  bays/parking_bay_TEMPLATE.yaml
  bays/parking_bay_01.yaml     Example devices
  STABILITY.md                 Power, PSRAM, Wi-Fi
scripts/generate_aruco_markers.py
docker-compose.yml             Local run, not the HA add-on build
repository.yaml                HA add-on repository listing
```

The add-on image copies `src/` and `run.sh` only. ESPHome YAML is documentation and source for the devices; it is flashed from the ESPHome dashboard, not by this container.

## Runtime

`run.sh` runs under the HA supervisor (`bashio`). It maps add-on options to environment variables and sets `HA_TOKEN` from `SUPERVISOR_TOKEN` (injected because `homeassistant_api: true`). It does not read a `ha_token` option unless that legacy key happens to be present.

`src/main.py` then:

1. Opens SQLite at `/data/parking.db` and creates snapshot dirs under `/data/snapshots`.
2. Imports bays from `/data/options.json` (written by the supervisor from the add-on configuration).
3. Connects MQTT and publishes discovery plus last-known state for every bay.
4. Starts an APScheduler interval job (`snapshot_interval_minutes`) that calls `analyze_all_bays`.
5. Fires one analysis immediately in the background so the first result is not delayed by a full interval.
6. Serves FastAPI on `0.0.0.0:8099`.

Ingress is enabled. The UI reads the `X-Ingress-Path` header and prefixes `fetch()` URLs. `StripTrailingSlashMiddleware` exists because HA Ingress follows 307 redirects badly; mutating routes also have POST aliases (`POST /api/bays/{id}`, `POST /api/bays/{id}/delete`, `POST /api/bays/{id}/expected-car`) so the UI does not rely on PUT or DELETE.

`/data` is the add-on data volume:

| Path | Contents |
|------|----------|
| `/data/options.json` | Add-on options, including `bays` |
| `/data/parking.db` | Bays, fleet, last analysis per bay |
| `/data/snapshots/<bay_id>/<UTC timestamp>.jpg` | Every capture. Nothing deletes old files. |

`config.py` also accepts a legacy `/data/addon_bays.json` if `options.json` has no `bays` key. The web UI does not write that file.

## Request path for one bay

`analyze_all_bays` in `analyzer.py` re-imports add-on bays, loads the fleet, then for each bay in `sort_order, name`:

1. Sleeps `capture_delay_seconds` before every bay except the first.
2. `ha_client.fetch_snapshot(camera_entity_id, dest)`.
3. `_analyze_bay_image` reads the JPEG and updates SQLite and MQTT.

A failure on one bay is recorded in `errors` and the loop continues.

### Snapshot (`ha_client.py`)

`fetch_snapshot`:

1. Requires `HA_TOKEN`. Without it, raises immediately.
2. Calls Home Assistant `script.turn_on` on `script.<camera_suffix>_prepare_capture` or `..._prepare_capture_flash` when `flash_before_capture` is true. The suffix is the entity id with the `camera.` prefix removed, so `camera.parking_bay_11` calls `script.parking_bay_11_prepare_capture`. A 404 is logged and ignored so a camera without that script still gets a snapshot. Any other prepare error is logged and the capture continues.
3. Waits `prepare_capture_wait_ms` (the script returns before the ESP32 has finished settling).
4. Discards `snapshot_warmup_frames` GETs. The OV2640 wakes cold because ESPHome `idle_framerate` is 0.
5. GETs `/api/camera_proxy/<entity>?t=<ms>` and writes the body to disk. HTTP 500, 502, 503, 504, 408, and 429 are retried up to `snapshot_max_attempts` with `snapshot_retry_delay_seconds` between tries. Other HTTP errors are not retried.

The ESPHome side of prepare is `script.prepare_capture` in `esphome/parking_bay_base.yaml`, exposed as an API service of the same name. Flash is a separate script that runs prepare and then toggles the GPIO4 LED.

### Analysis (`occupancy.py`, `aruco.py`, `bay_matching.py`)

Order matters. Color runs first. The marker never turns an empty bay into occupied.

1. **Dark frame.** Mean grayscale below 18 (`DARK_FRAME_MEAN_THRESHOLD`) means the shop lights are off. If this bay already has an analyzed state, that state is returned and the database is not touched. If there is no prior state, the function returns occupied false and still does not write.

2. **Color occupancy** (`detect_occupied_by_color`). A 50 px vertical band at the horizontal center is sampled. The bottom 10% of that band is dropped. Red is OpenCV HSV hue 0–12 and 168–180, saturation ≥ 50, value ≥ 40. Occupied when red pixels are at least 10% of the band (`RED_RATIO_OCCUPIED_THRESHOLD`). Gray (low saturation, mid value) is measured and logged only; it does not decide the result.

   This is site-specific. It assumes red car roofs and a gray concrete floor, with the camera aimed so the roof falls in the center strip. Another body color, or a camera aimed at the bumper, will not work without changing `occupancy.py`.

3. **Empty.** If the band is not red enough, the bay is stored as unoccupied, car and marker cleared, confidence 0. Transition from occupied to empty sets `car_left_at`.

4. **Marker**, only when the band says occupied. `analyze_image` in `aruco.py`:
   - Builds several images: original, CLAHE, bilateral+CLAHE, contrast steps 1.3 / 1.7 / 2.2, and a softer contrast for blown highlights.
   - On each image, runs the OpenCV ArUco detector twice: as captured, and horizontally flipped. ESP32-CAM JPEGs are often mirrored.
   - Stops early when a **fleet** marker’s confidence reaches 0.8 (`OCCUPIED_CONFIDENCE_THRESHOLD`).
   - Discards any ID that is not in the `fleet` table. Decorative patterns that happen to decode are ignored. An empty fleet means no marker is ever accepted.
   - Confidence is not the detector’s Hamming score. `_marker_confidence` scores marker area (relative to 0.5% of the frame) and how square the quad is.

5. **Sticky marker.** If this frame is occupied by color but no fleet marker clears 0.8, and the previous state had a fleet marker at confidence ≥ 0.8, that previous ID and car number are kept (`marker_sticky`). One glare frame does not forget which car is parked.

6. **Correct car** (`compute_correct_car`):

   | Result | Condition |
   |--------|-----------|
   | `uncertain` | Bay has no `expected_car_number` |
   | `no` | Not occupied, or occupied by a different fleet car |
   | `unknown` | Occupied, but no marker id and no car number (color only) |
   | `yes` | Occupied and `car_number` equals the expected car, or the detected ArUco ID is the fleet ID of that expected car |

7. **Timestamps.** Empty → occupied sets `car_parked_at`. Occupied → empty sets `car_left_at`. Other transitions leave both timestamps as they were. Values are UTC ISO-8601.

8. **MQTT.** `publish_bay_state` sends discovery (retained) and state (retained) for that bay.

`analyze_single_bay` is the same path for one bay and always fetches a fresh snapshot when the UI calls it. `capture_bay_snapshot` only saves a JPEG; it does not analyze.

## Data model (`database.py`)

SQLite, three tables. Migrations in `_migrate` add columns and rebuild an old `fleet` table that used `license_plate` (v1). That rebuild drops plate rows; it does not convert them.

**bays**

| Column | Notes |
|--------|--------|
| `id` | Primary key. Slug of `camera_entity_id`: lowercased, `.` and spaces become `_`. Example: `camera_parking_bay_11`. |
| `name` | Display name |
| `camera_entity_id` | Unique. HA entity such as `camera.parking_bay_11` |
| `sort_order` | Analyze-all order |
| `expected_car_number` | Fleet car that should be here. Null if unset. |

`sync_addon_bays` upserts from add-on options. If the YAML entry omits `expected_car_number`, the value already in the database is kept. A present value overwrites it.

**fleet**

| Column | Notes |
|--------|--------|
| `car_number` | Primary key. This is the number published to MQTT. |
| `aruco_id` | Unique. Must match the printed marker and the configured dictionary. |
| `notes` | Free text |

**bay_states** — one row per bay, last result only. No history table.

`repair_all_bay_ids` fills blank ids on old rows. The UI calls it from **Repair bay database** and also before listing bays.

## MQTT (`mqtt_publisher.py`)

Connects at startup with paho-mqtt to `mqtt_broker:mqtt_port`. If the connection fails, the client is set to null and the add-on still serves the UI; analysis results stay in SQLite only.

Discovery topics (retained):

```
homeassistant/<binary_sensor|sensor>/<bay_slug>/<object_id>/config
```

State topics (retained), prefix from `mqtt_topic_prefix` (default `parking_spot`):

```
parking_spot/<bay_id>/occupied          ON | OFF
parking_spot/<bay_id>/car_number        number | unknown
parking_spot/<bay_id>/aruco_id          number | none
parking_spot/<bay_id>/confidence        percent number, already ×100
parking_spot/<bay_id>/correct_car       yes | no | unknown | uncertain
parking_spot/<bay_id>/expected_car      number | none
parking_spot/<bay_id>/car_parked_at     ISO timestamp or empty
parking_spot/<bay_id>/car_left_at       ISO timestamp or empty
parking_spot/<bay_id>/state             JSON of the same fields
```

`car_number` in the JSON is null when unknown. The scalar topic uses the string `unknown`. Confidence in JSON is 0–1; the scalar topic is percent.

Discovery is published again on every state update, not only once.

## HTTP API

All routes are under `/api`. The UI is `GET /`. Health is `GET /health`.

| Method | Path | Role |
|--------|------|------|
| GET | `/api/status` | Effective settings (no secrets) |
| GET | `/api/bays` | Bay rows |
| POST | `/api/bays` | Create. 409 if camera or id exists. |
| POST | `/api/bays/{id}` | Update (ingress-safe) |
| PUT | `/api/bays/{id}` | Update |
| POST | `/api/bays/{id}/delete` | Delete (ingress-safe) |
| DELETE | `/api/bays/{id}` | Delete |
| POST | `/api/bays/{id}/expected-car` | Body `{ "expected_car_number": 1 }` or null |
| GET | `/api/fleet` | Fleet rows |
| POST | `/api/fleet` | Upsert `{ car_number, aruco_id, notes }` |
| DELETE | `/api/fleet/{car_number}` | Remove a car. Does not recompute bay state. |
| GET | `/api/dashboard` | Bays left-joined to last state, plus snapshot URL |
| POST | `/api/analyze` | All bays |
| POST | `/api/analyze/{bay_id}` | One bay, fresh snapshot |
| POST | `/api/analyze/{bay_id}/debug` | Re-read the latest file on disk. Returns vote counts, preprocess pass, red ratio, sticky flag. Does not write state. |
| POST | `/api/snapshots/{bay_id}/capture` | Fresh JPEG only |
| GET | `/api/snapshots/{bay_id}/latest?fetch=true` | Capture, or `fetch=false` for the newest file |
| POST | `/api/import-addon-bays` | Re-read options and publish MQTT |
| POST | `/api/mqtt/publish` | Discovery + last state for every bay |
| POST | `/api/bays/repair` | Fill blank bay ids |

Static snapshots are served at `/data/...` from `settings.data_dir`.

## Configuration map

Add-on option (`config.yaml` schema) → `run.sh` env var → `Settings` field.

| Option | Env | Default |
|--------|-----|---------|
| `ha_url` | `HA_URL` | `http://supervisor/core` |
| (supervisor) | `HA_TOKEN` | from `SUPERVISOR_TOKEN` |
| `snapshot_interval_minutes` | `SNAPSHOT_INTERVAL` | 5 |
| `capture_delay_seconds` | `CAPTURE_DELAY` | 3 |
| `flash_before_capture` | `FLASH_BEFORE_CAPTURE` | false |
| `prepare_capture_wait_ms` | `PREPARE_CAPTURE_WAIT_MS` | 3000 |
| `snapshot_warmup_frames` | `SNAPSHOT_WARMUP_FRAMES` | 1 |
| `snapshot_max_attempts` | `SNAPSHOT_MAX_ATTEMPTS` | 3 |
| `snapshot_retry_delay_seconds` | `SNAPSHOT_RETRY_DELAY` | 5 |
| `aruco_dictionary` | `ARUCO_DICTIONARY` | `DICT_4X4_50` |
| `mqtt_enabled` | `MQTT_ENABLED` | true |
| `mqtt_broker` | `MQTT_BROKER` | `core-mosquitto` |
| `mqtt_port` | `MQTT_PORT` | 1883 |
| `mqtt_username` | `MQTT_USERNAME` | empty |
| `mqtt_password` | `MQTT_PASSWORD` | empty |
| `mqtt_topic_prefix` | `MQTT_TOPIC_PREFIX` | `parking_spot` |

`bays` is not an environment variable. The supervisor writes the whole options document to `/data/options.json`. `Settings.load_addon_bays` accepts a list or a single object (the HA form editor sometimes emits one bay as a dict).

Dictionary names implemented in `ARUCO_DICTIONARIES` (`aruco.py`) are the `DICT_4X4_*`, `DICT_5X5_*`, `DICT_6X6_*`, and `DICT_7X7_100` / `DICT_7X7_250` sets. An unknown name silently falls back to `DICT_4X4_50`. Changing dictionary without reprinting markers will mis-read IDs. `DICT_4X4_50` only contains IDs 0–49.

`scripts/generate_aruco_markers.py` defaults to that dictionary, a 200 px marker, and a 30 px white quiet zone. The quiet zone is required.

## ESPHome contract

The add-on does not speak to the ESP32 directly. It only needs:

- A `camera.<slug_with_underscores>` entity that answers `camera_proxy`.
- Optional `script.<slug>_prepare_capture` and `script.<slug>_prepare_capture_flash`.

`esphome/parking_bay_base.yaml` is included as a package from each file in `esphome/bays/`. Per-bay files only override `substitutions` (`device_slug`, `friendly_name`, `bay_label`, `ha_camera_entity`, `debug_snapshot_file`). Camera defaults: 640×480, manual exposure, `idle_framerate` 0, quad PSRAM, clock and socket settings described in `esphome/STABILITY.md`.

`esphome/parking_bay_esp32cam.yaml` is an old entry point equivalent to bay 1. New bays should be copies of the template.

`secrets.yaml.example` must use the key `fallback_password`. That is the name `parking_bay_base.yaml` references for the fallback AP. `secrets.yaml` is gitignored by the ESPHome workflow and must not be committed.

## Local run

`docker-compose.yml` builds the same Dockerfile and publishes port 8099. It does not run inside the supervisor, so you must pass a real Home Assistant URL and a long-lived token:

```bash
# .env next to docker-compose.yml (do not commit it)
HA_URL=http://host.docker.internal:8123
HA_TOKEN=your_long_lived_access_token

docker compose up --build
```

Compose sets `MQTT_ENABLED=false`. Point `MQTT_BROKER` at a broker if you want to test discovery. `DATA_DIR` is the `parking_data` volume, so options.json will not exist unless you mount one. Add bays through the UI at `http://localhost:8099`.

OpenCV comes from Alpine (`py3-opencv` in the Dockerfile), not from pip. `requirements.txt` does not pin `opencv-python`. Marker behavior follows whatever OpenCV that base image ships.

## Where to change behavior

| Goal | File |
|------|------|
| Red / gray thresholds, center-strip width, dark cutoff | `src/occupancy.py` |
| Dictionary, confidence 0.8, glare passes, flip, sticky marker | `src/aruco.py` |
| yes / no / unknown / uncertain rules | `src/bay_matching.py` |
| Capture retries, prepare script name, warmup | `src/ha_client.py` |
| Schedule, what gets stored, parked/left timestamps | `src/analyzer.py` |
| New MQTT entity | `src/mqtt_publisher.py` (discovery list and `publish_bay_state`) |
| New add-on option | `config.yaml` schema **and** `run.sh` **and** `config.py` |
| Camera exposure, resolution, flash pin | `esphome/parking_bay_base.yaml` |
| UI labels and buttons | `src/templates/index.html`, `src/static/app.js` |

After an add-on option or Python change, bump `version` in `parking_spot_monitor/config.yaml` so Home Assistant offers the update.

## Things that look like bugs and are intentional

- Occupied can be true with car number unknown. Color and marker are separate questions.
- A marker ID that is not in Fleet is dropped, not reported as “unknown marker”.
- A dark frame does not write `occupied = false`.
- While the roof stays red, a missed read reuses the last good marker.
- Fleet delete does not clear `bay_states`. The next analyze applies the new fleet.
- Snapshots accumulate on disk for as long as the add-on volume lives.
- The scheduler does not wait for the previous round to finish. A slow round of many bays can overlap the next interval. `capture_delay_seconds` and camera timeouts dominate runtime (prepare wait + warmup + up to 90 s per HTTP call).
