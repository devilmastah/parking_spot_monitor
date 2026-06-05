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
export MQTT_ENABLED="$(bashio::config 'mqtt_enabled' true)"
export MQTT_BROKER="$(bashio::config 'mqtt_broker' 'core-mosquitto')"
export MQTT_PORT="$(bashio::config 'mqtt_port' 1883)"
export MQTT_USERNAME="$(bashio::config 'mqtt_username' '')"
export MQTT_PASSWORD="$(bashio::config 'mqtt_password' '')"
export MQTT_TOPIC_PREFIX="$(bashio::config 'mqtt_topic_prefix' 'parking_spot')"

if bashio::config.has_value "SUPERVISOR_TOKEN"; then
  export HA_TOKEN="${SUPERVISOR_TOKEN}"
elif bashio::config.has_value "ha_token"; then
  export HA_TOKEN="$(bashio::config 'ha_token')"
fi

mkdir -p "${DATA_DIR}/snapshots"

# Export addon camera list for the app to import
CAMERAS_JSON="$(bashio::config 'cameras' '[]')"
echo "${CAMERAS_JSON}" > "${DATA_DIR}/addon_cameras.json"

python3 -m src.main
