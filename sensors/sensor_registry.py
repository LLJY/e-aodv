import os
import subprocess
import random
from sensors.sensor import Sensor
from typing import Any, Optional, Dict
import logging

logger = logging.getLogger(__name__)


class SensorRegistry:
    """Registry for managing sensors in the E-AODV network"""

    def __init__(self):
        """Initialize the sensor registry"""
        self.sensors = {}
        logger.info("Sensor registry initialized")

    def register_sensor(self, sensor: Sensor) -> bool:
        """
        Register a sensor with the registry

        Args:
            sensor: The sensor to register

        Returns:
            True if successful, False otherwise
        """
        if not isinstance(sensor, Sensor):
            logger.error(f"Cannot register {type(sensor)}: not a Sensor")
            return False

        self.sensors[sensor.name] = sensor
        logger.info(f"Registered sensor: {sensor.name} (writable: {sensor.writable})")
        return True

    def unregister_sensor(self, sensor_name: str) -> bool:
        """
        Unregister a sensor from the registry

        Args:
            sensor_name: The name of the sensor to unregister

        Returns:
            True if successful, False otherwise
        """
        if sensor_name in self.sensors:
            del self.sensors[sensor_name]
            logger.info(f"Unregistered sensor: {sensor_name}")
            return True
        else:
            logger.warning(f"Cannot unregister {sensor_name}: not found")
            return False

    def get_sensor(self, sensor_name: str) -> Optional[Sensor]:
        """
        Get a sensor by name

        Args:
            sensor_name: The name of the sensor to get

        Returns:
            The sensor if found, None otherwise
        """
        return self.sensors.get(sensor_name)

    def read_sensor(self, sensor_name: str) -> Optional[Any]:
        """
        Read a sensor value

        Args:
            sensor_name: The name of the sensor to read

        Returns:
            The sensor value if successful, None otherwise
        """
        sensor = self.get_sensor(sensor_name)
        if sensor and sensor.enabled:
            try:
                return sensor.read()
            except Exception as e:
                logger.error(f"Error reading sensor {sensor_name}: {e}")
        return None

    def write_sensor(self, sensor_name: str, value: Any) -> bool:
        """
        Write a value to a sensor

        Args:
            sensor_name: The name of the sensor to write to
            value: The value to write

        Returns:
            True if successful, False otherwise
        """
        sensor = self.get_sensor(sensor_name)
        if not sensor:
            logger.error(f"Sensor {sensor_name} not found")
            return False

        if not sensor.enabled:
            logger.error(f"Sensor {sensor_name} is disabled")
            return False

        if not sensor.writable:
            logger.error(f"Sensor {sensor_name} is not writable")
            return False

        try:
            return sensor.write(value)
        except Exception as e:
            logger.error(f"Error writing to sensor {sensor_name}: {e}")
            return False

    def get_all_capabilities(self) -> Dict[str, Any]:
        """
        Get capability information for all registered sensors

        Returns:
            Dict of capabilities
        """
        capabilities = {}
        for sensor in self.sensors.values():
            capabilities.update(sensor.get_capability_info())
        return capabilities

    def read_all_sensors(self) -> Dict[str, Any]:
        """
        Read values from all enabled sensors

        Returns:
            Dict of sensor name -> value
        """
        values = {}
        for name, sensor in self.sensors.items():
            if sensor.enabled:
                try:
                    values[name] = sensor.read()
                except Exception as e:
                    logger.error(f"Error reading sensor {name}: {e}")
        return values

    def get_all_sensors_info(self) -> Dict[str, Dict[str, Any]]:
        """
        Get information about all registered sensors

        Returns:
            Dict of sensor name -> info dict
        """
        info = {}
        for name, sensor in self.sensors.items():
            info[name] = {
                "enabled": sensor.enabled,
                "writable": sensor.writable
            }
            if sensor.enabled:
                try:
                    info[name]["value"] = sensor.read()
                except Exception:
                    info[name]["value"] = None
        return info

    def cleanup(self) -> None:
        """Clean up resources used by sensors"""
        for sensor in self.sensors.values():
            try:
                if hasattr(sensor, 'cleanup') and callable(sensor.cleanup):
                    sensor.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up sensor {sensor.name}: {e}")