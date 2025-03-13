import logging
from sensors.sensor import Sensor
from typing import Union, Any

logger = logging.getLogger(__name__)


class LEDSensor(Sensor):
    """LED control sensor (actuator) for E-AODV"""

    def __init__(self, enabled: bool = True, simulate: bool = True):
        """
        Initialize the LED sensor

        Args:
            enabled: Whether the sensor is enabled
            simulate: Whether to simulate hardware (for testing)
        """
        super().__init__(name="led", enabled=enabled, writable=True)
        self.simulate = simulate
        self.state = False  # Current state (off by default)
        self.gpio_initialized = False

        # Initialize GPIO if not simulating
        if not simulate:
            try:
                self._setup_gpio()
            except Exception as e:
                logger.error(f"Failed to initialize GPIO for LED: {e}")

    def _setup_gpio(self) -> None:
        """Set up GPIO for LED control"""
        try:
            import RPi.GPIO as GPIO
            # Example GPIO setup
            self.led_pin = 17  # GPIO pin for LED
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.led_pin, GPIO.OUT)
            GPIO.output(self.led_pin, GPIO.LOW)
            self.gpio_initialized = True
            logger.info("LED GPIO initialized successfully")
        except ImportError:
            logger.warning("RPi.GPIO not available, LED will operate in simulation mode")
            self.simulate = True
        except Exception as e:
            logger.error(f"Error setting up GPIO: {e}")
            self.simulate = True

    def read(self) -> bool:
        """Read the current LED state"""
        return self.state

    def write(self, value: Any) -> bool:
        """
        Set LED state

        Args:
            value: True for on, False for off

        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert value to boolean
            if isinstance(value, str):
                if value.lower() in ('true', 'on', 'yes', '1'):
                    state = True
                elif value.lower() in ('false', 'off', 'no', '0'):
                    state = False
                else:
                    logger.error(f"Invalid LED state string: {value}")
                    return False
            else:
                state = bool(value)

            # Update internal state
            self.state = state

            # Control actual hardware if not in simulation
            if not self.simulate and self.gpio_initialized:
                try:
                    import RPi.GPIO as GPIO
                    GPIO.output(self.led_pin, GPIO.HIGH if state else GPIO.LOW)
                    logger.info(f"LED turned {'on' if state else 'off'}")
                except Exception as e:
                    logger.error(f"Error setting LED state: {e}")
                    return False
            else:
                logger.info(f"Simulated LED turned {'on' if state else 'off'}")

            return True

        except (ValueError, TypeError) as e:
            logger.error(f"Invalid value for LED state: {value} - {str(e)}")
            return False

    def cleanup(self) -> None:
        """Clean up GPIO resources"""
        if not self.simulate and self.gpio_initialized:
            try:
                import RPi.GPIO as GPIO
                GPIO.output(self.led_pin, GPIO.LOW)  # Turn off LED
                GPIO.cleanup(self.led_pin)
                logger.info("LED GPIO resources cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up LED GPIO: {e}")