import os
import subprocess
import random
from sensors.sensor import Sensor
from typing import Optional
import logging

logger = logging.getLogger(__name__)
class SensorRegistry:
    """Manages all sensors for E-AODV network"""
    
    def __init__(self):
        """Initialize the sensor registry"""
        self.sensors = {}
        logger.info("Initialized sensor registry")
        
    def register_sensor(self, sensor: Sensor):
        """Register a sensor with the registry"""
        self.sensors[sensor.name] = sensor
        logger.info(f"Registered sensor: {sensor.name}")
        
    def unregister_sensor(self, sensor_name: str):
        """Unregister a sensor from the registry"""
        if sensor_name in self.sensors:
            del self.sensors[sensor_name]
            logger.info(f"Unregistered sensor: {sensor_name}")
        
    def get_sensor(self, sensor_name: str) -> Optional[Sensor]:
        """Get a registered sensor by name"""
        return self.sensors.get(sensor_name)
        
    def get_all_capabilities(self):
        """Get capability information for all registered sensors"""
        capabilities = {}
        for sensor in self.sensors.values():
            capabilities.update(sensor.get_capability_info())
        return capabilities
        
    def read_all_sensors(self):
        """Read values from all enabled sensors"""
        sensor_data = {}
        for sensor in self.sensors.values():
            if sensor.enabled:
                try:
                    value = sensor.read()
                    sensor_data[sensor.name] = value
                except Exception as e:
                    logger.error(f"Error reading {sensor.name} sensor: {e}")
        return sensor_data