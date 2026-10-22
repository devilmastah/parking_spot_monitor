# Parking Spot Monitor — user guide

This guide is for the person who installs and runs the system in Home Assistant. You do not need to change code.

The system watches fixed parking bays. Each bay has its own ESP32-CAM. Each car has a printed ArUco marker on the roof. On a timer, the add-on takes one photo per bay, decides whether a car is there, reads the marker, and publishes the result to Home Assistant over MQTT.

## What you need

- Home Assistant OS or Supervised, with the **ESPHome** and **Mosquitto broker** add-ons
- The **MQTT** integration connected to that broker (**Settings → Devices & services → MQTT**)
- One **AI-Thinker ESP32-CAM** per bay, on a dedicated **5 V, 2 A** supply
- One printed **ArUco marker** per car, from the same dictionary the add-on uses (default `DICT_4X4_50`, marker IDs 0–49)

Cars at this site have **red roofs**. Empty bays show a **gray floor**. Occupied / empty is decided from that color, not from the marker alone. A different paint color needs a code change (see the developer guide).

## 1. Flash a camera for each bay

Configs live in `esphome/`. Flash the thin file under `esphome/bays/`, not `parking_bay_base.yaml`.

1. Copy `esphome/secrets.yaml.example` to `esphome/secrets.yaml` and fill in Wi-Fi and passwords. The firmware expects these keys:

   | Secret | Used for |
   |--------|----------|
   | `wifi_ssid` / `wifi_password` | Shop Wi-Fi |
   | `api_encryption_key` | ESPHome API (generate this in the ESPHome dashboard) |
   | `ota_password` | Over-the-air updates |
   | `fallback_password` | Fallback hotspot if Wi-Fi fails |

2. Copy `esphome/bays/parking_bay_TEMPLATE.yaml` to `esphome/bays/parking_bay_12.yaml` (use the real bay number).
3. Edit only the substitutions at the top:

   ```yaml
   substitutions:
     device_slug: parking-bay-12
     friendly_name: Parking Bay 12
     bay_label: "12"
     ha_camera_entity: camera.parking_bay_12
     debug_snapshot_file: /config/www/parking_bay_12_debug.jpg
   ```

   ESPHome turns hyphens into underscores, so `parking-bay-12` becomes the camera entity `camera.parking_bay_12`. Confirm the real entity ID under **Settings → Devices & services → Entities** after the device is adopted.

4. In the ESPHome dashboard, create a device from that file and flash it.
5. Repeat for every bay. Each device needs a unique `device_slug`.

Mount the camera overhead, aimed at where the roof marker sits. Do not leave the live camera stream open on a dashboard. The firmware is set up for still snapshots only; a constant stream heats the board and drops the Wi-Fi connection. Power and stability notes are in `esphome/STABILITY.md`.

After flashing, each device page in Home Assistant has:

| Control | What it does |
|---------|----------------|
| Exposure | Manual shutter, 0–1200. Higher is brighter. Start around 600–900. |
| Take snapshot | Saves a debug JPEG under `/config/www/` (open it as `/local/parking_bay_12_debug.jpg`) |
| Test Flash | Brief LED flash, for night checks |
| Restart | Remote reboot |

If daytime photos are dark, raise `camera_aec_value` in that bay’s YAML (try `"900"` or `"1050"`) and reflash. Leave the add-on option `flash_before_capture` off in daylight.

## 2. Print the markers

Markers must use the same dictionary as the add-on (`aruco_dictionary`, default `DICT_4X4_50`).

```bash
python scripts/generate_aruco_markers.py --start 1 --count 10
```

That writes PNGs into `aruco_markers/`, with a white border. Print them on matte paper, laminate, and fix one flat on each car roof. The marker should fill about 5–15% of the photo width.

Do not test by holding a marker up on a monitor. Screen moiré and glare make detection fail even when a paper marker would work.

Write down which marker ID is on which car. You enter that pairing in the Fleet tab.

## 3. Install the add-on

1. **Settings → Apps → App store → ⋮ → Repositories**
2. Add `https://github.com/devilmastah/parking_spot_monitor`
3. **Check for updates**, then install **Parking Spot Monitor**
4. Start it. The sidebar entry is **Parking Spots** (ingress on port 8099).

The add-on talks to Home Assistant with the supervisor token. You do not paste a long-lived access token in the add-on options. `homeassistant_api: true` in the add-on manifest is what provides that token. If the log says `ha_token=MISSING` or snapshots return 401, restart the add-on after confirming that line is still in `parking_spot_monitor/config.yaml`.

## 4. Add-on options

Open the add-on **Configuration** tab. Defaults are already set for a Home Assistant install with the Mosquitto add-on.

```yaml
ha_url: http://supervisor/core
snapshot_interval_minutes: 5
capture_delay_seconds: 3
flash_before_capture: false
prepare_capture_wait_ms: 3000
snapshot_warmup_frames: 1
snapshot_max_attempts: 3
snapshot_retry_delay_seconds: 5
aruco_dictionary: DICT_4X4_50
mqtt_enabled: true
mqtt_broker: core-mosquitto
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_topic_prefix: parking_spot
bays:
  - name: Bay 1
    camera_entity_id: camera.parking_bay_1
    expected_car_number: 1
  - name: Bay 11
    camera_entity_id: camera.parking_bay_11
    expected_car_number: 9
```

| Option | What it does | Default |
|--------|----------------|---------|
| `ha_url` | Home Assistant API. Leave `http://supervisor/core` on HA OS. | `http://supervisor/core` |
| `snapshot_interval_minutes` | How often a full round of all bays runs (1–60). | `5` |
| `capture_delay_seconds` | Pause between bays so only one camera is active (1–30). Raise to 5 if cameras drop off Wi-Fi. | `3` |
| `flash_before_capture` | Turn the ESP32 flash LED on for the shot. Use only on dark bays. | `false` |
| `prepare_capture_wait_ms` | How long to wait after waking the camera before taking the photo (500–10000). | `3000` |
| `snapshot_warmup_frames` | Extra frames fetched and thrown away so the sensor can settle (0–3). | `1` |
| `snapshot_max_attempts` | Retries if Home Assistant returns a temporary error (1–10). | `3` |
| `snapshot_retry_delay_seconds` | Wait between those retries (1–30). | `5` |
| `aruco_dictionary` | OpenCV dictionary name. Must match the printed markers. | `DICT_4X4_50` |
| `mqtt_enabled` | Publish discovery and state to MQTT. | `true` |
| `mqtt_broker` | Broker hostname. `core-mosquitto` is the official Mosquitto add-on. | `core-mosquitto` |
| `mqtt_port` | Broker port. | `1883` |
| `mqtt_username` / `mqtt_password` | Only if Mosquitto requires a login. | empty |
| `mqtt_topic_prefix` | Prefix for state topics. | `parking_spot` |
| `bays` | List of bays. Each entry needs `name` and `camera_entity_id`. `expected_car_number` is optional. | `[]` |

`expected_car_number` is the fleet car that **should** park in that bay. It is a car number, not the ArUco ID.

Changing options requires an add-on restart. Bay list changes are also picked up from **Settings → Import bays from add-on config** in the web UI, which does not need a restart if the add-on is already running. A restart always imports `bays` from the options again.

Capture order follows `sort_order` (set in the web UI). Bays that only exist in the YAML list are imported with sort order 0 and then ordered by name.

You can also add bays only in the web UI and leave `bays: []`. Those bays live in the add-on’s database and survive restarts. They are not written back into the add-on YAML.

## 5. Web UI

Open **Parking Spots** in the sidebar.

### Fleet

**Fleet → Add car**

| Field | Meaning |
|-------|---------|
| Car number | The number you use for that vehicle (this is what MQTT reports) |
| ArUco ID | The number printed on that car’s marker |
| Notes | Optional |

Each car number and each ArUco ID can only be used once. A marker that is not in this list is ignored, even if the camera sees it.

### Configure bays

Either:

- **Settings → Import bays from add-on config**, if you filled in `bays` in the add-on YAML, or
- **Configure bays → Add bay**, and type the camera entity ID yourself (`camera.parking_bay_11`)

Then set **Expected car** to the fleet car that belongs in that spot. **Sort order** controls the order of **Analyze all** (0 runs first).

Use **Snapshot** on a bay to check framing before you trust the schedule.

### Dashboard

| Button | What it does |
|--------|----------------|
| Take snapshot | Saves a new photo. Does not update occupied / car number. |
| Analyze | Takes a new photo and runs detection for that bay. |
| Analyze all | Does that for every bay, one after another, with `capture_delay_seconds` between them. |

The photo on the card is the latest snapshot. The occupied / car / confidence text is from the last **Analyze** run, which may be older than the photo if you only took a snapshot.

Auto-refresh (30 seconds) reloads the page data. It does not take new photos.

### Settings

| Button | When to use it |
|--------|----------------|
| Import bays from add-on config | After you edit the `bays` list in the add-on configuration |
| Publish MQTT discovery | Entities did not show up under the MQTT integration |
| Repair bay database | Edit or Delete fails on an old bay that has a blank id |

The **System** block is read-only. It shows the interval, dictionary, and MQTT flag the running process is using. Change those in the add-on configuration, then restart.

## 6. What shows up in Home Assistant

On startup, and after every analysis, the add-on publishes MQTT discovery. Look under the MQTT integration for a device named **Parking &lt;bay name&gt;** (for example **Parking Bay 1**).

Each bay gets these entities:

| Entity | Values |
|--------|--------|
| Occupied | `ON` or `OFF` |
| Car Number | Fleet car number, or `unknown` |
| ArUco ID | Marker ID, or `none` |
| Confidence | 0–100% |
| Expected Car | Car number assigned to this bay, or `none` |
| Correct Car | `yes`, `no`, `unknown`, or `uncertain` |
| Car Parked At | UTC timestamp when the bay went from empty to occupied |
| Car Left At | UTC timestamp when the bay went from occupied to empty |

State is also on `parking_spot/<bay_id>/occupied`, `.../car_number`, and so on. `<bay_id>` is the camera entity id with dots turned into underscores, for example `camera_parking_bay_11`.

### Occupied

A vertical strip in the middle of the photo is checked:

- Enough **red** (about 10% of that strip) means a car is there → occupied ON.
- Otherwise the bay is treated as empty floor → occupied OFF.
- The bottom 10% of the strip is ignored (cable tray / hose in the foreground).
- A nearly black frame (lights off) does **not** clear the bay. The previous result is kept.

### Correct car

| Value | Meaning |
|-------|---------|
| `yes` | Occupied, and the detected car (or its marker ID) is the expected car |
| `no` | Empty, or occupied by a different fleet car |
| `unknown` | Occupied by color, but no fleet marker could be read |
| `uncertain` | This bay has no expected car set |

If the marker is missed on one frame but the roof still looks red, the previous marker is kept. That avoids flipping to `unknown` because of one bad photo while the same car is still parked.

### MQTT not appearing

1. MQTT integration is set up and connected to the same broker (`core-mosquitto`).
2. At least one bay exists (import or Add bay).
3. Restart the add-on, or click **Publish MQTT discovery**.
4. If Mosquitto has authentication on, set `mqtt_username` and `mqtt_password` and restart.

## Day to day

The scheduler runs by itself every `snapshot_interval_minutes`. You only need the web UI to add a car, move a car to a different bay, or check a bad photo.

When a car is added or a marker is replaced:

1. Print the new marker from the same dictionary.
2. **Fleet → Add car** (or edit the existing car so the ArUco ID matches).
3. **Configure bays** → set **Expected car** on that bay.

When a new bay is added:

1. Flash a new ESPHome device from the template.
2. Add it to the add-on `bays` list (or Add bay in the UI) with the real `camera.*` entity.
3. Import or restart, set the expected car, take a snapshot, then Analyze.

## Troubleshooting

| What you see | What to check |
|--------------|----------------|
| Snapshot fails, log says 401 or token missing | Restart the add-on. Confirm `homeassistant_api: true` is in the add-on config. |
| Snapshot fails, HTTP 500/502 from the camera | Camera offline, brownout, or Wi-Fi. See `esphome/STABILITY.md`. Increase `capture_delay_seconds`. |
| Photo is black or very dark | Exposure slider, or `camera_aec_value` in the bay YAML. A dark frame will not mark the bay empty. |
| Occupied stays off with a car in the bay | The roof in the center of the frame is not red enough, or the camera is not aimed at the roof. This detector expects red roofs and a gray floor. |
| Occupied ON, car number `unknown`, correct car `unknown` | Color says occupied, but the marker was not read or is not in Fleet. Check framing, border, and the Fleet ArUco ID. |
| Wrong car number | Two cars sharing an ID, or the wrong dictionary. `aruco_dictionary` must match the print. |
| Camera reboots during capture | Power supply. Use 5 V 2 A and a short cable. |
| Edit / Delete does nothing on an old bay | **Settings → Repair bay database**. |

Add-on logs: **Settings → Apps → Parking Spot Monitor → Log**. Look for `Analysis complete`, `Dark frame`, `Marker detected`, and `Bay empty by color slice`.
