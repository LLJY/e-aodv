# sensor_implementations.py
import logging
import random
import time
from typing import Any, Optional, Dict
import serial  # Import serial here

try:
    import RPi.GPIO as GPIO
    import adafruit_dht
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("RPi.GPIO or adafruit_dht not available. Hardware interaction will be simulated.")

from sensors.sensor import Sensor

logger = logging.getLogger(__name__)

class DHT11Sensor(Sensor):
    def __init__(self, pin: int, enabled: bool = True, simulate: bool = False):
        super().__init__(name="dht11", enabled=enabled, writable=False)  # DHT11 is not writable
        self.pin = pin
        self.simulate = simulate
        self.dht_device = None

        if not self.simulate and GPIO_AVAILABLE:
            try:
                self.dht_device = adafruit_dht.DHT11(pin)
            except Exception as e:
                logger.error(f"Failed to initialize DHT11: {e}")
                self.simulate = True  # Fallback to simulation

    def read(self) -> Dict[str, float]:
        """Return humidity and temperature readings."""
        if not self.enabled:
            return {}

        if self.simulate:
            return {
                'temperature': round(random.uniform(18, 30), 1),
                'humidity': round(random.uniform(40, 80), 1)
            }

        try:
            if self.dht_device:
                temperature = self.dht_device.temperature
                humidity = self.dht_device.humidity
                if humidity is not None and temperature is not None:
                    return {
                        'temperature': round(temperature, 1),
                        'humidity': round(humidity, 1)
                    }
            logger.warning("DHT11 read returned None values.")
            return {}
        except Exception as e:
            logger.error(f"DHT11 read error: {e}")
            return {}  # Return empty dict on error, not None

class WaterLevelSensor(Sensor):
    def __init__(self, pin: int, enabled: bool = True, simulate: bool = False):
        super().__init__(name="water_level", enabled=enabled, writable=False)  # water level is not writable
        self.pin = pin
        self.simulate = simulate

        if not self.simulate and GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def read(self) -> Optional[bool]:
        """Return True if water level is high (float switch activated)."""
        if not self.enabled:
            return None  # Return None if sensor is disabled

        if self.simulate:
            return random.choice([True, False])

        if GPIO_AVAILABLE:
            return GPIO.input(self.pin) == GPIO.LOW
        return None

class TouchSensor(Sensor):
    def __init__(self, pin: int, enabled: bool = True, simulate: bool = False):
        super().__init__(name="touch", enabled=enabled, writable=False)  # Touch sensor is not writable
        self.pin = pin
        self.simulate = simulate

        if not self.simulate and GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.IN)

    def read(self) -> Optional[bool]:
        """Return True when touched."""
        if not self.enabled:
            return None

        if self.simulate:
            return random.choice([True, False])

        if GPIO_AVAILABLE:
            return GPIO.input(self.pin) == GPIO.HIGH
        return None

class ServoSensor(Sensor):
    """Servo motor actuator."""

    def __init__(self, pin: int = 18, enabled: bool = True, simulate: bool = False):
        super().__init__(name="servo", enabled=enabled, writable=True)
        self.pin = pin
        self.simulate = simulate
        self.current_angle = 0
        self.pwm = self._initialize_pwm()

    def _initialize_pwm(self):
        """Initialize PWM for servo."""
        if not self.simulate and GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            pwm = GPIO.PWM(self.pin, 50)  # 50 Hz
            pwm.start(0)
            return pwm
        return None

    def read(self) -> int:
        return self.current_angle

    def write(self, angle: Any) -> bool:
        try:
            angle = int(angle)
            if not 0 <= angle <= 180:
                logger.error(f"Invalid servo angle: {angle}. Must be 0-180.")
                return False
            self.current_angle = angle
            if not self.simulate:
                duty = (angle / 18) + 2
                self.pwm.ChangeDutyCycle(duty)
                time.sleep(0.5)
                self.pwm.ChangeDutyCycle(0)
            return True
        except ValueError:
            logger.error(f"Invalid servo angle: {angle}. Must be an integer.")
            return False
        except Exception as e:
            logger.error(f"Servo write error: {e}")
            return False

    def cleanup(self):
        if self.pwm:
            self.pwm.stop()
        if GPIO_AVAILABLE:
            GPIO.cleanup(self.pin)


class ColorLEDSensor(Sensor):
    def __init__(self, color: str, pin: int, enabled: bool = True, simulate: bool = False):
        super().__init__(name=f"led_{color.lower()}", enabled=enabled, writable=True)
        self.pin = pin
        self.color = color
        self.simulate = simulate
        self.state = False  # LED initially off

        if not self.simulate and GPIO_AVAILABLE:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.pin, GPIO.OUT)
            GPIO.output(self.pin, GPIO.LOW) # initialize to low

    def read(self) -> bool:
        """Return current LED state."""
        return self.state

    def write(self, value: Any) -> bool:
        """Set LED state (True/False or equivalent)."""
        try:
            if isinstance(value, str):
                self.state = value.lower() in ('true', 'on', '1', 'yes')
            else:
                self.state = bool(value)

            if not self.simulate:
                if GPIO_AVAILABLE:
                    GPIO.output(self.pin, GPIO.HIGH if self.state else GPIO.LOW)
            return True
        except Exception as e:
            logger.error(f"LED write error: {e}")
            return False
    def cleanup(self):
        """Clean up the LED pin"""
        if GPIO_AVAILABLE:
            GPIO.cleanup(self.pin)

class SoilMoistureSensor(Sensor):
    def __init__(self, port: str = '/dev/ttyUSB0', baud_rate: int = 9600,
                 enabled: bool = True, simulate: bool = False):
        super().__init__(name="soil_moisture", enabled=enabled, writable=False)
        self.port = port
        self.baud_rate = baud_rate
        self.simulate = simulate
        self.arduino = self._initialize_arduino()

    def _initialize_arduino(self):
        """Initialize serial connection to Arduino."""
        if not self.simulate:
            try:
                return serial.Serial(self.port, self.baud_rate, timeout=1)
            except serial.SerialException as e:
                logger.error(f"Arduino connection failed ({self.port}, {self.baud_rate}): {e}")
                #  Optionally set simulate = True here to fall back to simulation
        return None

    def read(self) -> Optional[str]:
        if not self.enabled:
            return None

        if self.simulate:
            return f"{random.uniform(0, 1023):.1f}"  # Simulate a reading

        if self.arduino and self.arduino.in_waiting > 0:
            try:
                return self.arduino.readline().decode('utf-8').strip()
            except UnicodeDecodeError:
                logger.error("Corrupted data received from Arduino")
        return None

    def cleanup(self):
        if self.arduino:
            self.arduino.close()

# --- Initialization Function ---
def initialize_sensors(sensor_registry: 'SensorRegistry', simulate: bool = False):
    """Initialize and register all sensors."""

    # remove sensors you dont need here
    # explicitly define the gpio pins (if required)
    sensors = [
        DHT11Sensor(pin=2, simulate=simulate),     # GPIO2 (using D2 on board)
        WaterLevelSensor(pin=26, simulate=simulate),  # GPIO26
        TouchSensor(pin=3, simulate=simulate),       # GPIO3
        ServoSensor(pin=18, simulate=simulate),      # GPIO18
        ColorLEDSensor(color="red", pin=20, simulate=simulate),    # GPIO20
        ColorLEDSensor(color="green", pin=16, simulate=simulate),  # GPIO16
        ColorLEDSensor(color="yellow", pin=21, simulate=simulate), # GPIO21
        SoilMoistureSensor(port="/dev/ttyUSB0", simulate=simulate)
    ]

    for sensor in sensors:
        sensor_registry.register_sensor(sensor)
        logger.info(f"Registered sensor: {sensor.name}")

    return sensors  # Return the list of initialized sensors