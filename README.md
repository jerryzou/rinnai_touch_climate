# rinnai_touch_climate
HA custom component for Rinnai Wifi Module. It only supports brivis/rinnai evap cooling system at the moment.
 
The following configuration.yaml entries are required:

```
climate:
  - platform: rinnai_touch_climate
    name: Rinnai Evap Cooler
    host: <IP_ADDRESS>
    port: 27847
    scan_interval: 1800
    temperature_sensor: <TEMP_SENSOR_ENTITY>

logger:
  logs:
    custom_components.rinnai_touch_climate: debug
```
