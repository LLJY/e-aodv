from abc import ABC, abstractmethod
from typing import Optional, Any, Dict
import logging

logger = logging.getLogger(__name__)


class Sensor(ABC):
    """Base class for all sensors in the E-AODV network"""

    def __init__(self, name: str, enabled: bool = True, writable: bool = False):
        """
        Initialize the sensor

        Args:
            name: The sensor name (used as identifier)
            enabled: Whether the sensor is enabled
            writable: Whether the sensor supports write operations
        """
        self.name = name
        self.enabled = enabled
        self.writable = writable
        logger.info(f"Initialized {name} sensor (enabled: {enabled}, writable: {writable})")

    @abstractmethod
    def read(self):
        """Read the current sensor value"""
        pass

    def write(self, value: Any) -> bool:
        """
        Write a value to the sensor (for actuators)

        Args:
            value: The value to write

        Returns:
            True if successful, False otherwise
        """
        if not self.writable:
            logger.error(f"Sensor {self.name} is not writable")
            return False

        logger.warning(f"Write not implemented for sensor {self.name}")
        return False

    def get_capability_info(self) -> Dict[str, Any]:
        """Get capability information for the E-AODV protocol"""
        capability_info = {
            self.name: self.enabled,
            f"{self.name}_writable": self.writable
        }

        # If enabled, add the current value
        if self.enabled:
            try:
                value = self.read()
                capability_info[f"{self.name}_value"] = value
            except Exception as e:
                logger.error(f"Error reading {self.name} sensor: {e}")

        return capability_info