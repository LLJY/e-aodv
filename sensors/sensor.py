from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)

class Sensor(ABC):
    """Base class for all sensors in the E-AODV network"""
    
    def __init__(self, name: str, enabled: bool = True):
        """Initialize the sensor"""
        self.name = name
        self.enabled = enabled
        logger.info(f"Initialized {name} sensor (enabled: {enabled})")
    
    @abstractmethod
    def read(self):
        """Read the current sensor value"""
        pass
    
    def get_capability_info(self):
        """Get capability information for the E-AODV protocol"""
        capability_info = {
            self.name: self.enabled
        }
        
        # If enabled, add the current value
        if self.enabled:
            try:
                value = self.read()
                capability_info[f"{self.name}_value"] = value
            except Exception as e:
                logger.error(f"Error reading {self.name} sensor: {e}")
                
        return capability_info