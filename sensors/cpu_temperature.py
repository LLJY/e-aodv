import os
import subprocess
import random
from sensors.sensor import Sensor
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class CPUTemperatureSensor(Sensor):
    """CPU temperature sensor for Linux/Raspberry Pi"""
    
    def __init__(self, enabled: bool = True, simulate: bool = False):
        """Initialize the CPU temperature sensor"""
        super().__init__(name="temperature", enabled=enabled)
        self.simulate = simulate
        self.writable = False
        
    def read(self) -> float:
        """Read the current CPU temperature"""
        if not self.enabled:
            return 0.0
            
        if self.simulate:
            return round(random.uniform(40, 70), 1)
            
        # Try different methods to get the temperature
        try:
            # Method 1: Read from thermal zone (most Linux systems)
            if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = float(f.read().strip()) / 1000.0
                    return round(temp, 1)
            
            # Method 2: Use vcgencmd for Raspberry Pi
            try:
                output = subprocess.check_output(['vcgencmd', 'measure_temp'], timeout=1).decode()
                temp_str = output.strip().replace('temp=', '').replace('\'C', '')
                return float(temp_str)
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
                
            # Method 3: Fallback to simulated value
            logger.warning("Could not get CPU temperature, using simulated value")
            return round(random.uniform(40, 70), 1)
            
        except Exception as e:
            logger.error(f"Error reading CPU temperature: {e}")
            return 25.0  # Default value on error