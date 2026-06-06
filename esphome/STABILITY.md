# ESP32-CAM stability guide

Frequent reboots or HA disconnects on ESP32-CAM are very common. Usually it is **hardware or camera load**, not Home Assistant.

## #1 Power supply (most common)

ESP32-CAM spikes **300–500 mA** when the camera captures. Weak power causes brownout reboots.

- Use a **dedicated 5 V 2 A** supply (not a marginal USB phone charger)
- Short, thick USB cable — voltage drop on thin/long cables causes resets
- Do **not** power from a flaky 3.3 V regulator only
- Add a **100–470 µF** cap near the module if you see `Brownout detector` in logs

## #2 Camera running too much (config)

The camera heats the board and fills WiFi buffers if it streams continuously.

Our config is tuned for **snapshot-only**:

```yaml
idle_framerate: 0 fps      # no background captures
max_framerate: 0.2 fps
camera_resolution: 640x480
external_clock:
  frequency: 10MHz         # not 20MHz — less interference on many boards
```

**Do not** open the live camera stream in HA dashboards 24/7 — that keeps the camera active and hot.

## #3 PSRAM mismatch

Wrong PSRAM mode → random crashes.

| Module | Setting |
|--------|---------|
| AI-Thinker ESP32-CAM (classic) | `psram: mode: quad` |
| No PSRAM clone | **remove** `psram:` block, use `640x480` |

## #4 WiFi

- `power_save_mode: none` — already set (good for reliability)
- Mount near AP or use AP with good 2.4 GHz coverage
- Metal roof / far bay → disconnects; consider external antenna ESP32-CAM if available
- `fast_connect: false` — can help flaky reconnects (now default in our YAML)

## #5 HA / ESPHome integration

- Avoid viewing **multiple** camera streams at once
- Parking Spot Monitor already captures **one bay at a time** — good
- Increase `capture_delay_seconds` to **5** if you add more bays

## #6 Check the logs

**ESPHome → device → LOGS** after a reboot. Look for:

| Message | Likely cause |
|---------|----------------|
| `Brownout detector was triggered` | Power supply |
| `Guru Meditation Error` | Memory / PSRAM / bug |
| `cam init failed` | Power, wiring, or wrong board pins |
| `Cannot send message... TCP buffer` | Camera + API overload → lower resolution / idle 0 fps |
| `Task watchdog` | Camera blocked too long |

**Home Assistant → Settings → System → Logs** → filter `esphome` for timeout messages.

## #7 What we changed in `parking_bay_esp32cam.yaml`

- Lower resolution default (`640x480`)
- Camera clock **10 MHz** (was 20 MHz)
- **`idle_framerate: 0 fps`** — no constant background frames
- **`CONFIG_LWIP_MAX_SOCKETS=16`** — fewer API drops under load
- Removed `captive_portal` (saves RAM; re-add only for initial WiFi setup)
- Removed periodic `text_sensor` (unnecessary polling)
- Added **Restart** button in HA for remote recovery

## If still unstable

1. Drop to `320x240` temporarily — if stable, it was memory/bandwidth
2. Confirm PSRAM: ESPHome log at boot should mention PSRAM detected
3. Try a different physical ESP32-CAM (clones vary)
4. Keep `flash_before_capture: false` in the add-on (less load)

ArUco detection works fine at 640×480 when the marker is sized appropriately in frame (~5–15% of image width).
