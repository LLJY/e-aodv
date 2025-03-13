import logging
from sensors.sensor import Sensor
from typing import Optional, Union

logger = logging.getLogger(__name__)


class MotorSensor(Sensor):
    """Motor control sensor (actuator) for E-AODV"""

    def __init__(self, enabled: bool = True, simulate: bool = True):
        """
        Initialize the motor sensor

        Args:
            enabled: Whether the sensor is enabled
            simulate: Whether to simulate hardware (for testing)
        """
        super().__init__(name="motor", enabled=enabled, writable=True)
        self.simulate = simulate
        self.speed = 0  # Current speed value (0-100)
        self.gpio_initialized = False

        # Initialize GPIO if not simulating
        if not simulate:
            try:
                self._setup_gpio()
            except Exception as e:
                logger.error(f"Failed to initialize GPIO for motor: {e}")

    def _setup_gpio(self) -> None:
        """Set up GPIO for motor control"""
        try:
            import RPi.GPIO as GPIO
            # Example GPIO setup
            self.motor_pin = 18  # PWM pin for motor
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.motor_pin, GPIO.OUT)
            self.pwm = GPIO.PWM(self.motor_pin, 100)  # 100 Hz
            self.pwm.start(0)  # Start with 0% duty cycle
            self.gpio_initialized = True
            logger.info("Motor GPIO initialized successfully")
        except ImportError:
            logger.warning("RPi.GPIO not available, motor will operate in simulation mode")
            self.simulate = True
        except Exception as e:
            logger.error(f"Error setting up GPIO: {e}")
            self.simulate = True

    def read(self) -> int:
        """Read the current motor speed"""
        return self.speed

    def write(self, value: Union[int, float, str]) -> bool:
        """
        Set motor speed

        Args:
            value: Speed value (0-100)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert value to integer if it's a string or float
            if isinstance(value, str):
                speed = int(float(value))
            elif isinstance(value, float):
                speed = int(value)
            else:
                speed = value

            # Validate range
            if not 0 <= speed <= 100:
                logger.error(f"Invalid motor speed: {speed}. Must be 0-100.")
                return False

            # Update internal state
            self.speed = speed

            # Control actual hardware if not in simulation
            if not self.simulate and self.gpio_initialized:
                try:
                    self.pwm.ChangeDutyCycle(speed)
                    logger.info(f"Motor speed set to {speed}%")
                except Exception as e:
                    logger.error(f"Error setting motor speed: {e}")
                    return False
            else:
                logger.info(f"Simulated motor speed set to {speed}%")

            return True

        except (ValueError, TypeError) as e:
            logger.error(f"Invalid value for motor speed: {value} - {str(e)}")
            return False

    def cleanup(self) -> None:
        """Clean up GPIO resources"""
        if not self.simulate and self.gpio_initialized:
            try:
                import RPi.GPIO as GPIO
                self.pwm.stop()
                GPIO.cleanup(self.motor_pin)
                logger.info("Motor GPIO resources cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up motor GPIO: {e}")