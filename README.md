# Parking Spot Monitor — Home Assistant Add-on

Monitor fixed parking bays using **one ESP32-CAM per bay** (ESPHome) and **ArUco markers** on car roofs. Bays are photographed **one at a time** to keep WiFi load manageable.

**Handover docs**

| Guide | Who it is for |
|-------|----------------|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Install cameras, print markers, set add-on options, use the web UI, read MQTT entities |
| [docs/DEVELOPER.md](docs/DEVELOPER.md) | How the add-on is structured, how detection works, where to change behavior |
| [esphome/README.md](esphome/README.md) | Per-bay ESPHome files and exposure tuning |
| [esphome/STABILITY.md](esphome/STABILITY.md) | ESP32-CAM power, Wi-Fi, and reboot causes |

Occupancy is based on a **red roof** against a **gray floor** in the center of the frame. The marker then identifies which fleet car it is. Details and the `correct_car` values (`yes`, `no`, `unknown`, `uncertain`) are in the user guide.

## How it works

```mermaid
flowchart LR
  A[Scheduler] -->|bay 1 snapshot| CAM1[ESP32-CAM bay 1]
  A -->|wait| A
  A -->|bay 2 snapshot| CAM2[ESP32-CAM bay 2]
  CAM1 --> ARU[ArUco detect]
  CAM2 --> ARU
  ARU --> FLEET[Match marker ID → car #]
  FLEET --> MQTT[MQTT entities in HA]
```

1. Each parking bay has its own ESP32-CAM (ESPHome → `camera.*` entity in HA)
2. The add-on triggers snapshots **sequentially** (configurable delay between bays)
3. OpenCV detects the ArUco marker on the car roof
4. Marker ID is matched to your fleet → car number
5. Results published via MQTT to Home Assistant

## Why this approach

- **Dedicated camera per bay** → close-up, high resolution on the marker
- **ArUco markers** → reliable vs license plate OCR on wide-angle security cams
- **Sequential capture** → avoids overloading WiFi with multiple ESP32 streams at once

## Installation

1. **Settings → Apps → App store → ⋮ → Repositories**
2. Add: `https://github.com/devilmastah/parking_spot_monitor`
3. **Check for updates** → install **Parking Spot Monitor**

## Setup guide

Full steps, every add-on option, and troubleshooting: [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

### 1. Flash ESP32-CAM units (ESPHome)

Use the ESPHome config in [`esphome/parking_bay_esp32cam.yaml`](esphome/parking_bay_esp32cam.yaml) — see [`esphome/README.md`](esphome/README.md) for per-bay setup.

- One ESPHome device per bay (unique `name` / `friendly_name`)
- Mount camera overhead or angled at the roof marker spot
- Recommended resolution: `800x600` or higher (with PSRAM)

Generate ArUco markers (must match `aruco_dictionary` in add-on config, default **DICT_4X4_50**):

```bash
python scripts/generate_aruco_markers.py --start 1 --count 10
```

Or manually:

```bash
python3 -c "
import cv2
from cv2 import aruco
d = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
for i in range(1, 11):
    img = aruco.generateImageMarker(d, i, 200)
    img = cv2.copyMakeBorder(img, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    cv2.imwrite(f'aruco_{i}.png', img)
"
```

Print and mount one marker on each car roof (laminated, high contrast).

### 2. Add-on configuration

Example (YAML mode):

```yaml
ha_url: http://supervisor/core
snapshot_interval_minutes: 5
capture_delay_seconds: 3
flash_before_capture: false
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
  - name: Bay 2
    camera_entity_id: camera.parking_bay_2
    expected_car_number: 2
```

| Option | Description | Default |
|--------|-------------|---------|
| `snapshot_interval_minutes` | Full round-robin interval | `5` |
| `capture_delay_seconds` | Pause between bay snapshots | `3` |
| `flash_before_capture` | Call ESPHome flash script before snapshot (only needed in low light) | `false` |
| `aruco_dictionary` | OpenCV dictionary name | `DICT_4X4_50` |
| `bays` | List of `{name, camera_entity_id, expected_car_number?}` | `[]` |

**Capture order** follows `sort_order` in the Web UI (or list order from config).

### 3. Web UI

Open the add-on Web UI:

1. **Settings → Import bays from add-on config**
2. **Fleet → Add car** — car number + ArUco ID (same ID printed on the marker)
3. **Configure bays → Edit** — set **Expected car number** for each fixed spot
4. **Bays → Snapshot** on each bay to verify framing
5. **Analyze all bays** → check **Live Status**

### 4. MQTT entities (per bay)

- `binary_sensor.*_occupied` — ON/OFF
- `sensor.*_car_number` — detected fleet car number
- `sensor.*_aruco_id` — detected marker ID
- `sensor.*_confidence` — detection confidence (%)
- `sensor.*_expected_car` — car number assigned to this bay
- `sensor.*_correct_car` — `yes`, `no`, or `uncertain`

**correct_car logic**

| Value | Meaning |
|-------|---------|
| `yes` | Occupied, and the detected car matches the bay's expected car |
| `no` | Empty, or occupied by a different fleet car |
| `unknown` | Occupied (red roof) but no fleet marker could be read |
| `uncertain` | No expected car configured for this bay |

### MQTT troubleshooting

Discovery uses Home Assistant's standard MQTT auto-discovery (`homeassistant/.../config` topics).

1. **MQTT integration** must be set up in HA (**Settings → Devices & services → MQTT**) and connected to the same broker (`core-mosquitto`).
2. **At least one bay** must be configured (add-on config or Web UI → Import bays).
3. After updating, **restart the add-on** — discovery is published on startup (v2.3.2+).
4. Or use **Settings → Publish MQTT discovery** in the Web UI.
5. Look for a device named **Parking Bay 1** (etc.) under the MQTT integration.
6. If Mosquitto requires login, set `mqtt_username` / `mqtt_password` in add-on config (create a user in the Mosquitto add-on or use your HA MQTT credentials).

Entity topics: `parking_spot/<bay_id>/occupied`, `.../correct_car`, etc.

## Marker tips

- Use **DICT_4X4_50** for up to 50 cars (IDs 0–49)
- Generate markers with the script above — include the **white border** (quiet zone)
- **Print on paper** and mount flat on the roof — do **not** test by showing a marker on a monitor (moire, glare, and skew break detection)
- ESP32-CAM snapshots are often **horizontally mirrored**; v2.2.1+ tries both orientations automatically. You can also set `horizontal_mirror: true` in ESPHome to fix it at the camera
- Marker should fill ~5–15% of the frame width; aim the ESP32-CAM fairly straight down
- Add each car in **Fleet** with the same ArUco ID printed on its marker
- A marker ID that is not in **Fleet** is ignored
- Good lighting helps; enable `flash_before_capture: true` only for dark bays
- Empty bay = the center of the frame is not red enough (gray floor). A dark frame (lights off) keeps the previous result
- Occupied with no readable marker stays occupied and reports car number `unknown`

## Development

See [docs/DEVELOPER.md](docs/DEVELOPER.md). Local run (needs `HA_URL` and `HA_TOKEN`; MQTT is off in compose):

```bash
docker compose up --build
```

## License

MIT
