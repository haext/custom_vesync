"""Support for VeSync bulbs and wall dimmers."""
import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import (
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin,
)

from .common import VeSyncDevice, has_feature
from .const import DEV_TYPE_TO_HA, DOMAIN, VS_DISCOVERY, VS_LIGHTS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lights."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    @callback
    def discover(devices):
        """Add new devices to platform."""
        _setup_entities(devices, async_add_entities, coordinator)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, VS_DISCOVERY.format(VS_LIGHTS), discover)
    )

    _setup_entities(
        hass.data[DOMAIN][config_entry.entry_id][VS_LIGHTS],
        async_add_entities,
        coordinator,
    )


@callback
def _setup_entities(devices, async_add_entities, coordinator):
    """Check if device is online and add entity."""
    entities = []
    for dev in devices:
        if DEV_TYPE_TO_HA.get(dev.device_type) in ("walldimmer", "bulb-dimmable"):
            entities.append(VeSyncDimmableLightHA(dev, coordinator))
        if DEV_TYPE_TO_HA.get(dev.device_type) in ("bulb-tunable-white",):
            entities.append(VeSyncTunableWhiteLightHA(dev, coordinator))
        if hasattr(dev, "night_light") and dev.night_light:
            entities.append(VeSyncNightLightHA(dev, coordinator))

    async_add_entities(entities, update_before_add=True)


def _vesync_brightness_to_ha(vesync_brightness):
    try:
        brightness_value = int(vesync_brightness)
    except ValueError:
        _LOGGER.debug(
            "VeSync - received unexpected 'brightness' value from pyvesync api: %s",
            vesync_brightness,
        )
        return None
    return round((max(1, brightness_value) / 100) * 255)


def _ha_brightness_to_vesync(ha_brightness):
    brightness = int(ha_brightness)
    brightness = max(1, min(brightness, 255))
    brightness = round((brightness / 255) * 100)
    return max(1, min(brightness, 100))


class VeSyncBaseLight(VeSyncDevice, LightEntity):
    """Base class for VeSync Light Devices Representations."""

    @property
    def brightness(self):
        """Get light brightness."""
        return _vesync_brightness_to_ha(self.device.brightness)

    def turn_on(self, **kwargs):
        """Turn the device on."""
        attribute_adjustment_only = False

        # Handle Color Temperature (Kelvin)
        if self.color_mode == ColorMode.COLOR_TEMP and ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            # Ensure within Kelvin bounds
            kelvin = max(self.min_color_temp_kelvin, min(kelvin, self.max_color_temp_kelvin))
            
            # VeSync API expects 0-100 percentage (0=Cold, 100=Warm)
            # Map Kelvin to Percent
            mireds = color_temperature_kelvin_to_mired(kelvin)
            color_temp_pct = round(
                ((mireds - self.min_mireds) / (self.max_mireds - self.min_mireds)) * 100
            )
            # Flip logic for pyvesync (100 - pct)
            color_temp_pct = 100 - color_temp_pct
            color_temp_pct = max(0, min(color_temp_pct, 100))
            
            self.device.set_color_temp(color_temp_pct)
            attribute_adjustment_only = True

        # Handle Brightness
        if ATTR_BRIGHTNESS in kwargs:
            brightness = _ha_brightness_to_vesync(kwargs[ATTR_BRIGHTNESS])
            self.device.set_brightness(brightness)
            attribute_adjustment_only = True

        if attribute_adjustment_only:
            return
        
        self.device.turn_on()


class VeSyncDimmableLightHA(VeSyncBaseLight):
    """Representation of a VeSync dimmable light device."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}


class VeSyncTunableWhiteLightHA(VeSyncBaseLight):
    """Representation of a VeSync Tunable White Light device."""

    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}

    @property
    def color_temp_kelvin(self):
        """Get device white temperature in Kelvin."""
        result = self.device.color_temp_pct
        try:
            pct = int(result)
        except (ValueError, TypeError):
            return self.min_color_temp_kelvin
        
        # Convert percent back to Mireds
        pct = 100 - pct
        mireds = self.min_mireds + ((self.max_mireds - self.min_mireds) / 100 * pct)
        return color_temperature_mired_to_kelvin(mireds)

    @property
    def min_mireds(self): return 154  # 6500K
    @property
    def max_mireds(self): return 370  # 2700K

    @property
    def min_color_temp_kelvin(self): return 2700
    @property
    def max_color_temp_kelvin(self): return 6500


class VeSyncNightLightHA(VeSyncDimmableLightHA):
    """Representation of the night light on a VeSync device."""

    def __init__(self, device, coordinator) -> None:
        """Initialize the VeSync device."""
        super().__init__(device, coordinator)
        self.device = device
        self.has_brightness = has_feature(self.device, "details", "night_light_brightness")

    @property
    def unique_id(self):
        return f"{super().unique_id}-night-light"

    @property
    def name(self):
        return f"{super().name} night light"

    @property
    def brightness(self):
        if self.has_brightness:
            return _vesync_brightness_to_ha(self.device.details["night_light_brightness"])
        return {"on": 255, "dim": 125, "off": 0}.get(self.device.details["night_light"], 0)

    @property
    def is_on(self):
        if has_feature(self.device, "details", "night_light"):
            return self.device.details["night_light"] in ["on", "dim"]
        if self.has_brightness:
            return self.device.details.get("night_light_brightness", 0) > 0
        return False

    @property
    def entity_category(self):
        return EntityCategory.CONFIG

    def turn_on(self, **kwargs):
        if self.device._config_dict.get("module") in VS_FAN_TYPES:
            if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] < 255:
                self.device.set_night_light("dim")
            else:
                self.device.set_night_light("on")
        elif ATTR_BRIGHTNESS in kwargs:
            self.device.set_night_light_brightness(_ha_brightness_to_vesync(kwargs[ATTR_BRIGHTNESS]))
        else:
            self.device.set_night_light_brightness(100)

    def turn_off(self, **kwargs):
        if self.device._config_dict.get("module") in VS_FAN_TYPES:
            self.device.set_night_light("off")
        else:
            self.device.set_night_light_brightness(0)
