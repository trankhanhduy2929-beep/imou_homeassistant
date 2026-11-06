#!/usr/bin/with-contenv bashio

if ! bashio::services.available "mqtt"; then
    bashio::exit.nok "Home Assistant MQTT service is required"
fi

export IMOU_ACCOUNT="$(bashio::config 'account')"
export IMOU_PASSWORD="$(bashio::config 'password')"
export IMOU_POLL_INTERVAL="$(bashio::config 'poll_interval')"
export IMOU_REQUEST_TIMEOUT="$(bashio::config 'request_timeout')"
export IMOU_MAX_PROPERTIES="$(bashio::config 'max_properties')"
export IMOU_SNAPSHOT_INTERVAL="$(bashio::config 'snapshot_interval')"
export IMOU_EVENT_IMAGES="$(bashio::config 'event_images')"
export LOG_LEVEL="$(bashio::config 'log_level')"

export MQTT_HOST="$(bashio::services mqtt 'host')"
export MQTT_PORT="$(bashio::services mqtt 'port')"
export MQTT_USERNAME="$(bashio::services mqtt 'username')"
export MQTT_PASSWORD="$(bashio::services mqtt 'password')"
export MQTT_SSL="false"

exec python3 -m imou_bridge

