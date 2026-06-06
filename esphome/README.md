# ESPHome configs for Parking Spot Monitor

One **ESP32-CAM (AI-Thinker)** per parking bay, running this config.

## Quick start

1. Install the [ESPHome add-on](https://esphome.io/guides/getting_started_hassio.html) in Home Assistant (if not already).

2. Copy this folder into your ESPHome config path, or paste `parking_bay_esp32cam.yaml` via **ESPHome → New device → Edit**.

3. Copy `secrets.yaml.example` → `secrets.yaml` and fill in WiFi + keys.

4. Edit **substitutions** at the top of `parking_bay_esp32cam.yaml` for this bay:

   ```yaml
   substitutions:
     device_slug: parking-bay-1      # MUST be unique per bay
     friendly_name: Parking Bay 1
     bay_label: "1"
   ```

5. Flash the device. In HA (via **ESPHome integration** + `api:`) you should get:
   - `camera.parking_bay_1`
   - `script.parking_bay_1_prepare_capture`
   - `button.parking_bay_1_test_flash`

   Do **not** add an empty `homeassistant:` block — that component is not valid YAML in current ESPHome.

6. Repeat for bay 2, 3, … with a **new copy** of the YAML (new `device_slug` each time).

## Per-bay checklist

| Bay | device_slug     | HA camera entity          |
|-----|-----------------|---------------------------|
| 1   | parking-bay-1   | camera.parking_bay_1      |
| 2   | parking-bay-2   | camera.parking_bay_2      |
| 3   | parking-bay-3   | camera.parking_bay_3      |

Entity IDs use underscores (ESPHome replaces `-` with `_`).

## Camera mounting

- Mount overhead or high angle looking at the **roof ArUco marker**
- Use **Test Flash** at night to verify exposure
- Use **Snapshot** in the Parking Spot Monitor Web UI to confirm marker size (aim for ~5–15% of frame width)

## Flash (optional)

The ESPHome config includes a flash LED script for **night / dark bays only**. With good daylight you do **not** need it.

In the add-on config leave:

```yaml
flash_before_capture: false
```

The add-on will fetch snapshots directly with no flash. The ESPHome **Test Flash** button remains for manual checks if you ever need it.

## PSRAM

**AI-Thinker ESP32-CAM** (classic ESP32): use `mode: quad` — already set in the YAML.

**Octal** PSRAM is for **ESP32-S3** boards only; it will fail to compile on ESP32-CAM.

If your module has **no PSRAM** (cheaper clones), comment out the whole `psram:` block and lower resolution:

```yaml
camera_resolution: 640x480
```

## Resolution

Default `800x600` works well on AI-Thinker boards with quad PSRAM. If images are soft, try:

```yaml
camera_resolution: 1024x768
camera_jpeg_quality: "10"
```

If the device crashes or reboots on capture, lower resolution or improve the 5 V power supply (ESP32-CAM is sensitive to voltage sag).

## Parking Spot Monitor add-on

Add each camera under **bays** in add-on configuration:

```yaml
bays:
  - name: Bay 1
    camera_entity_id: camera.parking_bay_1
  - name: Bay 2
    camera_entity_id: camera.parking_bay_2
flash_before_capture: true
capture_delay_seconds: 3
```

`capture_delay_seconds` should be long enough for WiFi recovery between ESP32 snapshots (3–5 s typical).
