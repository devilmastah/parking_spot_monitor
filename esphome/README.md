# ESPHome configs for Parking Spot Monitor

One **ESP32-CAM (AI-Thinker)** per parking bay.

## Template layout

```
esphome/
  parking_bay_base.yaml      ← shared hardware/camera/flash (do not flash directly)
  bays/
    parking_bay_TEMPLATE.yaml   ← copy this for each new bay
    parking_bay_01.yaml
    parking_bay_11.yaml         ← your working bay 11 example
  secrets.yaml                  ← your WiFi + keys (not in git)
  secrets.yaml.example
```

**Flash the thin file** under `bays/`, not the base file.

## Add a new bay (2 minutes)

1. Copy the template:
   ```
   bays/parking_bay_TEMPLATE.yaml  →  bays/parking_bay_12.yaml
   ```

2. Edit only the substitutions at the top:

   ```yaml
   substitutions:
     device_slug: parking-bay-12
     friendly_name: Parking Bay 12
     bay_label: "12"
   ```

3. In ESPHome: **New device** → paste `bays/parking_bay_12.yaml` (or point ESPHome dashboard at the file).

4. Copy `secrets.yaml.example` → `secrets.yaml` once per ESPHome config folder.

5. Flash. In Home Assistant you get e.g. `camera.parking_bay_12`.

6. In Parking Spot Monitor add-on → **Configure bays** → camera entity `camera.parking_bay_12`.

## Optional substitutions

Set these in the bay file only when you need to override defaults from `parking_bay_base.yaml`:

| Substitution | Default | Example override |
|--------------|---------|------------------|
| `camera_resolution` | `640x480` | `800x600` if PSRAM stable |
| `camera_jpeg_quality` | `"10"` | lower number = higher JPEG quality |
| `ap_ssid` | `${device_slug}-setup` | `"Parking-Bay-11 Fallback Hotspot"` |

Per-bay WiFi tweaks (add below `packages:` in the bay file):

```yaml
wifi:
  fast_connect: true
```

## Entity IDs in Home Assistant

ESPHome turns hyphens into underscores:

| device_slug | Typical camera entity |
|-------------|----------------------|
| parking-bay-1 | `camera.parking_bay_1` |
| parking-bay-11 | `camera.parking_bay_11` |

The camera `name` is `friendly_name`, so entities may also appear as `camera.parking_bay_11` when friendly name is used — check **Settings → Entities** after adoption.

## Per-bay checklist

| Bay | Flash this file | device_slug | HA camera (typical) |
|-----|-----------------|-------------|---------------------|
| 1 | `bays/parking_bay_01.yaml` | parking-bay-1 | camera.parking_bay_1 |
| 11 | `bays/parking_bay_11.yaml` | parking-bay-11 | camera.parking_bay_11 |

## Camera mounting

- Mount overhead or high angle at the **roof ArUco marker**
- Use **Test Flash** at night to verify exposure
- Use **Take snapshot** in the Parking Spot Monitor Web UI to confirm marker size (~5–15% of frame width)

## Flash (optional)

Leave add-on config `flash_before_capture: false` in daylight. The ESPHome **prepare_capture** script is only called when you enable flash in the add-on.

## PSRAM / stability

See [STABILITY.md](STABILITY.md). AI-Thinker uses **quad** PSRAM (`mode: quad` in base). Comment out `psram:` if your board has none.

## Parking Spot Monitor add-on

```yaml
bays:
  - name: Bay 11
    camera_entity_id: camera.parking_bay_11
    expected_car_number: 9
capture_delay_seconds: 3
```

Use the exact entity ID from Home Assistant (Developer Tools → States).
