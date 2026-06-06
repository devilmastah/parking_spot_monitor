#!/usr/bin/with-contenv bashio
set -e

export DATA_DIR="/data"
export PORT="8099"

if bashio::config.has_value "ha_url"; then
  export HA_URL="$(bashio::config 'ha_url')"
else
  export HA_URL="http://supervisor/core"
fi

export SNAPSHOT_INTERVAL="$(bashio::config 'snapshot_interval_minutes' 5)"
export CAPTURE_DELAY="$(bashio::config 'capture_delay_seconds' 3)"
export FLASH_BEFORE_CAPTURE="$(bashio::config 'flash_before_capture' true)"
export PREPARE_CAPTURE_WAIT_MS="$(bashio::config 'prepare_capture_wait_ms' 2000)"
export SNAPSHOT_MAX_ATTEMPTS="$(bashio::config 'snapshot_max_attempts' 3)"
export SNAPSHOT_RETRY_DELAY="$(bashio::config 'snapshot_retry_delay_seconds' 5)"
export ARUCO_DICTIONARY="$(bashio::config 'aruco_dictionary' 'DICT_4X4_50')"
export MQTT_ENABLED="$(bashio::config 'mqtt_enabled' true)"
export MQTT_BROKER="$(bashio::config 'mqtt_broker' 'core-mosquitto')"
export MQTT_PORT="$(bashio::config 'mqtt_port' 1883)"
export MQTT_USERNAME="$(bashio::config 'mqtt_username' '')"
export MQTT_PASSWORD="$(bashio::config 'mqtt_password' '')"
export MQTT_TOPIC_PREFIX="$(bashio::config 'mqtt_topic_prefix' 'parking_spot')"

# SUPERVISOR_TOKEN is injected by the Supervisor when homeassistant_api: true in config.yaml.
# Do NOT use bashio::config for this — it is an env var, not an add-on option.
if [ -n "${SUPERVISOR_TOKEN:-}" ]; then
  export HA_TOKEN="${SUPERVISOR_TOKEN}"
elif [ -n "${HASSIO_TOKEN:-}" ]; then
  export HA_TOKEN="${HASSIO_TOKEN}"
elif bashio::config.has_value "ha_token"; then
  export HA_TOKEN="$(bashio::config 'ha_token')"
else
  echo "WARNING: No HA API token (SUPERVISOR_TOKEN). Camera snapshots will fail with 401." >&2
fi

mkdir -p "${DATA_DIR}/snapshots"

python3 -m src.main
