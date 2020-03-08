"""
custom_component to support for Rinnai Touch thermostats - Evap Cooling Only.
 
The following configuration.yaml entries are required:

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
"""
import logging
import json
import socket
import time
import voluptuous as vol
from typing import Any, Dict, List, Optional

from homeassistant.components.climate import ClimateDevice, PLATFORM_SCHEMA
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF,
    HVAC_MODE_COOL,
    HVAC_MODE_FAN_ONLY,
    HVAC_MODE_AUTO,
    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_FAN_MODE,
    HVAC_MODES,
    ATTR_HVAC_MODE
)
from homeassistant.const import (
    CONF_NAME,
    CONF_HOST,
    CONF_PORT,
    STATE_ON,
    STATE_UNKNOWN,
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    PRECISION_HALVES,
    PRECISION_WHOLE
)

from homeassistant.helpers.typing import HomeAssistantType
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = 'Rinnai Evap Cooler'
DEFAULT_TIMEOUT = 10

CONF_TEMPERATURE_SENSOR = 'temperature_sensor'

SUPPORT_FLAGS = (
    SUPPORT_TARGET_TEMPERATURE | 
    SUPPORT_FAN_MODE
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string,
    vol.Optional(CONF_PORT, default=27847): cv.positive_int,
    vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
})

async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Setup the Rinnai touch thermostat."""
    async_add_entities([ThermostatDevice(hass, config)])

class ThermostatDevice(ClimateDevice, RestoreEntity):
    def __init__(self, hass, config):
        """Initialize the Rinnai touch climate device."""
        self.hass = hass
        self._data = None
        self._name = config.get(CONF_NAME)
        self._host = config.get(CONF_HOST)
        self._port = config.get(CONF_PORT)
        self._temperature_sensor = config.get(CONF_TEMPERATURE_SENSOR)

        self._operation_modes = [HVAC_MODE_OFF, HVAC_MODE_COOL, HVAC_MODE_FAN_ONLY, HVAC_MODE_AUTO]
        self._fan_modes = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12', '13', '14', '15', '16']
        
        self._min_temperature = 18.0
        self._max_temperature = 28.0
        self._precision = 1.0
        
        self._hvac_mode = HVAC_MODE_OFF
        self._current_fan_mode = self._fan_modes[0]
        self._target_temperature = self._min_temperature
        self._last_on_operation = None
        self._current_temperature = None
        
        self._unit = hass.config.units.temperature_unit
        self._support_flags = SUPPORT_FLAGS

        self._chkdata = None
        self._att_hvac_mode = None
        self._att_fan_mode = None

        self.update()
    
    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()
    
        last_state = await self.async_get_last_state()
        
        if last_state is not None:
            self._hvac_mode = last_state.state
            self._current_fan_mode = last_state.attributes['fan_mode']

            if 'last_on_operation' in last_state.attributes:
                self._last_on_operation = last_state.attributes['last_on_operation']

        if self._temperature_sensor:
            async_track_state_change(self.hass, self._temperature_sensor, self._async_temp_sensor_changed)

            temp_sensor_state = self.hass.states.get(self._temperature_sensor)
            if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                self._async_update_temp(temp_sensor_state)

    @property
    def should_poll(self):
        """Polling needed for thermostat."""
        return True

    def update(self) -> None:
        """Update local data with thermostat data."""
        _LOGGER.debug("***Performing an update***")
        time.sleep(1)
        connection = self.connectToTouch(self._host,self._port)
        if connection:
            self._data = self.getTouchData(connection)

            if not self._data:
                _LOGGER.debug("No data fetched.")
                return

            if 'ECOM' in self._data[1]:
            #Heat Mode
                _LOGGER.debug("Evap Mode")
                ecom = self._data[1].get("ECOM")
                self.evapMode(ecom)

            else:
                self._hvac_mode = HVAC_MODE_OFF

            _LOGGER.debug("Update completed")

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._support_flags

    @property
    def name(self) -> str:
        """Return the name of the thermostat."""
        return self._name

    @property
    def device_state_attributes(self) -> dict:
        """Platform specific attributes."""
        return {
            'last_on_operation': self._last_on_operation
        }

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit

    @property
    def min_temp(self):
        """Return the polling state."""
        return self._min_temperature
        
    @property
    def max_temp(self):
        """Return the polling state."""
        return self._max_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._precision
    
    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature."""
        return self._current_temperature

    @property
    def hvac_mode(self) -> str:
        """Return the current operation mode."""
        return self._hvac_mode

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._operation_modes

    @property
    def fan_modes(self):
        """Return the list of available fan modes."""
        return self._fan_modes

    @property
    def fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    def set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        _LOGGER.debug("*Updating hvac mode: %s", str(hvac_mode))

        connection = self.connectToTouch(self._host,self._port)
        if connection:

            self._att_hvac_mode = hvac_mode

            if not hvac_mode == HVAC_MODE_OFF:
                self._last_on_operation = hvac_mode
            
            if hvac_mode == "cool":
                self._chkdata = {"SW":"N","OP":"M","FS":"N","PS":"N"}
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"M","FS":"N","PS":"N"}}}')
                _LOGGER.debug("Data sent successfully.")
                self._hvac_mode = HVAC_MODE_COOL

            elif hvac_mode == "fan_only":
                self._chkdata = {"SW":"N","OP":"M","FS":"N","PS":"F"}
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"M","FS":"N","PS":"F"}}}')
                _LOGGER.debug("Data sent successfully.")
                self._hvac_mode = HVAC_MODE_FAN_ONLY

            elif hvac_mode == "auto":
                self._chkdata = {"SW":"N","OP":"A"}
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"A"}}}')
                _LOGGER.debug("Data sent successfully.")
                self._hvac_mode = HVAC_MODE_AUTO
            
            else:
                self._chkdata = {"SW":"F"}
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"SW":"F"}}}')
                _LOGGER.debug("Data sent successfully.")
                self._hvac_mode = HVAC_MODE_OFF
        
            connection.close
            _LOGGER.debug("Update of hvac mode completed")
            time.sleep(2)
        else:
            _LOGGER.debug("Connection failed")

    def retry_set_mode(self, hvac_mode):
        """Set new target hvac mode."""
        _LOGGER.debug("*Updating hvac mode: %s", str(hvac_mode))

        s = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        s.connect((self._host,self._port))

        msg = ''

        if not hvac_mode == HVAC_MODE_OFF:
            self._last_on_operation = hvac_mode
        
        if hvac_mode == "cool":
            msg = 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"M","FS":"N","PS":"N"}}}'
            self._hvac_mode = HVAC_MODE_COOL

        elif hvac_mode == "fan_only":
            msg = 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"M","FS":"N","PS":"F"}}}'
            self._hvac_mode = HVAC_MODE_FAN_ONLY

        elif hvac_mode == "auto":
            msg = 'N000001{"ECOM":{"GSO":{"SW":"N","OP":"A"}}}'
            self._hvac_mode = HVAC_MODE_AUTO
        
        else:
            msg = 'N000001{"ECOM":{"GSO":{"SW":"F"}}}'
            self._hvac_mode = HVAC_MODE_OFF
        _LOGGER.debug(msg.encode())     
        s.sendall(msg.encode()) 
        _LOGGER.debug("Data resent successfully.")
        s.close()
        _LOGGER.debug("Update of hvac mode completed")
        time.sleep(2)
        self.update()
        
    
    def set_fan_mode(self, fan_mode):
        """Set fan mode."""
        if self._hvac_mode == HVAC_MODE_COOL or self._hvac_mode == HVAC_MODE_FAN_ONLY:
            self._att_fan_mode = fan_mode
            connection = self.connectToTouch(self._host,self._port)
            if connection:
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"FL":'+fan_mode+'}}}')
                connection.close
                self._current_fan_mode = fan_mode
                _LOGGER.debug("Update fan level completed")
                time.sleep(2)
            else:
                _LOGGER.debug("Connection failed")

    def retry_fanmode(self, fan_mode):
        """Set fan mode."""
        if self._hvac_mode == HVAC_MODE_COOL or self._hvac_mode == HVAC_MODE_FAN_ONLY:
            self._att_fan_mode = fan_mode
            connection = self.connectToTouch(self._host,self._port)
            if connection:
                self.sendTouchData(connection, 'N000001{"ECOM":{"GSO":{"FL":'+fan_mode+'}}}')
                connection.close
                self._current_fan_mode = fan_mode
                _LOGGER.debug("Re-update fan level completed")
                time.sleep(2)
                self.update()
            else:
                _LOGGER.debug("Connection failed")    
    
    def evapMode(self, evapData):
        """Set all the evap cooler states."""
        _LOGGER.debug("Setting evap cooler states")
        
        gso = ''
        
        gso = evapData.get("GSO")
        if len(gso) > 0:

            if self._att_hvac_mode:
                if (gso.get("SW")) != self._chkdata.get("SW") or (gso.get("OP")) != self._chkdata.get("OP") or (gso.get("PS")) != self._chkdata.get("PS"):
                    _LOGGER.debug("Settings states failed, try again in 2s ...")
                    time.sleep(2)
                    self.retry_set_mode(self._att_hvac_mode)
                    return
                else:
                    self._att_hvac_mode = None

            if self._att_fan_mode:
                if (gso.get("FL")) != self._att_fan_mode:
                    _LOGGER.debug("Settings fan mode failed, try again in 2s ...")
                    time.sleep(2)
                    self.retry_fanmode(self._att_fan_mode)
                    return
                else:
                    self._att_fan_mode = None
            
            if (gso.get("SW")) == "N":
                if (gso.get("OP")) == "M":
                    if (gso.get("PS")) == "N":
                        self._hvac_mode = HVAC_MODE_COOL
                        _LOGGER.debug("Evap cooler is in manual cool mode.")
                    else:
                        self._hvac_mode = HVAC_MODE_FAN_ONLY
                        _LOGGER.debug("Evap cooler is in fan only mode.")
                    self._current_fan_mode = gso.get("FL")
                    _LOGGER.debug("Fan level: %s", self._current_fan_mode)
                else:
                    self._hvac_mode = HVAC_MODE_AUTO
                    _LOGGER.debug("Evap cooler is in auto mode.")
            else:
                #evap cooler is off
                _LOGGER.debug("Evap Cooler Off")
                self._hvac_mode = HVAC_MODE_OFF
    
    def set_temperature(self, **kwargs) -> None:
        """Set target temperature."""

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        
        if temperature < self._min_temperature or temperature > self._max_temperature:
            _LOGGER.warning('The temperature value is out of min/max range') 
            return

        if self._precision == PRECISION_WHOLE:
            self._target_temperature = round(temperature)
        else:
            self._target_temperature = round(temperature, 1)
        
        time.sleep(2)
    
    def getTouchData(self, client):

        time.sleep(1)
        try:
            reply = client.recv(4096)
        except socket.error as err:
            _LOGGER.debug("Error receiving data: %s", str(err))
            client.close()
            return

        _LOGGER.debug("Call result...")
        _LOGGER.debug(reply)
        valid_data_index = 0
        if reply.decode("utf-8").find('N000000'):
            valid_data_index = reply.decode("utf-8").find('N000000') + 7
        elif reply.decode("utf-8").find('N000001'):
            valid_data_index = reply.decode("utf-8").find('N000001') + 7
        else:
            _LOGGER.debug("No valid data found, re-update in 2s...")
            client.close()
            time.sleep(2)
            self.update()
            return
        
        _LOGGER.debug("Valid data index: %s", valid_data_index)
        if valid_data_index == 6:
            _LOGGER.debug("No valid data found, re-update in 2s...")
            client.close()
            time.sleep(2)
            self.update()
            return

        jStr = reply[valid_data_index:]
            
        if len(jStr) > 0:
            j = json.loads(jStr)
            client.close()
            return j
        else:
            _LOGGER.debug("Empty response") 
        client.close()
    
    def connectToTouch(self, touchHost, touchPort):
        """Connect the client"""
        _LOGGER.debug("Trying to connect to touch...")
        time.sleep(1)
        try: 
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except socket.error as err: 
            _LOGGER.debug("Failed to create socket: %s", str(err))
            client.close()
            return
        _LOGGER.debug("Socket created")

        try:
            client.connect((touchHost, touchPort))
        except socket.error as err:
            _LOGGER.debug("Error connecting to server: %s", str(err))
            client.close()
            return

        _LOGGER.debug("Connected")
        return client

    def sendTouchData(self, client, cmd):
        _LOGGER.debug(cmd.encode()) 
        try:
            client.send(cmd.encode())
        except socket.error as err:
            _LOGGER.debug("Error sending command: %s", str(err))
            client.close()
        
    async def _async_temp_sensor_changed(self, entity_id, old_state, new_state):
        """Handle temperature sensor changes."""
        if new_state is None:
            return

        self._async_update_temp(new_state)
        await self.async_update_ha_state()
    
    @callback
    def _async_update_temp(self, state):
        """Update thermostat with latest state from temperature sensor."""
        try:
            if state.state != STATE_UNKNOWN:
                self._current_temperature = float(state.state)
        except ValueError as ex:
            _LOGGER.error("Unable to update from temperature sensor: %s", ex)