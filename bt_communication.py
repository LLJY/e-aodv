#!/usr/bin/env python3
import bluetooth
import json
import threading
import time
import random
import logging
import sys
import socket
import subprocess
from typing import Dict, Any, List, Callable, Optional, Tuple, Set

# Binary Exponential Backoff Constants
MAX_CONNECT_RETRIES = 5  # Maximum number of retry attempts for normal connections
MAX_RECONNECT_RETRIES = 3  # Maximum retry attempts for disconnection recovery
BASE_BACKOFF_DELAY = 0.5  # Initial delay in seconds
MAX_BACKOFF_DELAY = 30.0  # Maximum delay cap in seconds
JITTER_RANGE = 0.25  # Jitter factor (±25%)


def calculate_backoff_delay(retry_count: int, base_delay: float = BASE_BACKOFF_DELAY,
                            max_delay: float = MAX_BACKOFF_DELAY, jitter: float = JITTER_RANGE) -> float:
    """
    Calculate binary exponential backoff delay with jitter.

    Args:
        retry_count: Current retry attempt (0-based)
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap in seconds
        jitter: Jitter factor (0-1) to randomize delay

    Returns:
        Delay in seconds
    """
    if retry_count <= 0:
        return 0

    # Calculate exponential delay: base_delay * 2^(retry_count-1)
    delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)

    # Add jitter to avoid synchronization issues
    jitter_factor = 1.0 + random.uniform(-jitter, jitter)
    actual_delay = delay * jitter_factor

    return actual_delay


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Define constants
EAODV_UUID = "94f39d29-7d6d-437d-973b-fba39e49d4ee"  # Custom UUID for E-AODV service
BUFFER_SIZE = 1024
RFCOMM_PORT = 3  # Using port 3 instead of port 22 (SSH) to avoid conflicts


class BluezCommandRunner:
    """Handles all direct Bluetooth system commands"""

    def __init__(self):
        """Initialize the Bluez command runner"""
        self.scan_process = None

    def get_local_address(self) -> Optional[str]:
        """Get the Bluetooth address of the local adapter"""
        try:
            output = subprocess.check_output(["hciconfig", "hci0"], timeout=5).decode()
            for line in output.splitlines():
                if "BD Address" in line:
                    return line.split("Address:")[1].strip()
            return None
        except Exception as e:
            logger.error(f"Error getting local Bluetooth address: {e}")
            return None

    def get_paired_devices(self) -> Set[str]:
        """Get set of currently paired device addresses"""
        try:
            output = subprocess.check_output(["bluetoothctl", "devices", "Paired"], timeout=5).decode()
            result = set()
            for line in output.strip().split("\n"):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == "Device":
                        addr = parts[1]
                        result.add(addr)
            return result
        except Exception as e:
            logger.error(f"Failed to get paired devices: {e}")
            return set()

    def start_scan(self):
        """Start background scanning process"""
        try:
            logger.info("Starting background Bluetooth scan")
            if self.scan_process:
                self.stop_scan()

            self.scan_process = subprocess.Popen(
                ["bluetoothctl", "scan", "on"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception as e:
            logger.error(f"Failed to start scan: {e}")
            return False

    def stop_scan(self):
        """Stop background scanning process"""
        if self.scan_process:
            try:
                self.scan_process.terminate()
                self.scan_process.wait(timeout=3)
            except Exception:
                try:
                    self.scan_process.kill()
                except Exception:
                    pass
            self.scan_process = None

    def reset_adapter(self):
        """Reset the Bluetooth adapter"""
        try:
            subprocess.run(["hciconfig", "hci0", "reset"], check=False)
            return True
        except Exception as e:
            logger.error(f"Failed to reset adapter: {e}")
            return False

    def configure_adapter(self, device_name: str) -> bool:
        """Configure Bluetooth adapter settings"""
        try:
            # Reset adapter
            self.reset_adapter()
            time.sleep(1)

            # Configure basic settings
            subprocess.run(["hciconfig", "hci0", "up"], check=False)
            subprocess.run(["hciconfig", "hci0", "name", device_name], check=False)
            subprocess.run(["hciconfig", "hci0", "piscan"], check=False)

            # Use bluetoothctl for further configuration
            commands = (
                "power on\n"
                "discoverable on\n"
                "pairable on\n"
                "agent on\n"
                "default-agent\n"
                "quit\n"
            )
            process = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            process.communicate(input=commands.encode(), timeout=10)

            # Add service to SDP database
            subprocess.run(["sdptool", "add", f"--channel={RFCOMM_PORT}", "SP"], check=False)

            logger.info("Bluetooth adapter configured successfully")
            return True

        except Exception as e:
            logger.error(f"Error configuring Bluetooth: {e}")
            return False

    def discover_devices(self, duration: int = 8) -> List[Tuple[str, str]]:
        """Discover nearby Bluetooth devices"""
        try:
            return bluetooth.discover_devices(
                duration=duration,
                lookup_names=True,
                lookup_class=False,
                flush_cache=True
            )
        except Exception as e:
            logger.error(f"Error discovering devices: {e}")
            return []


class BTCommunication:
    """Bluetooth Classic Communication for E-AODV"""

    def __init__(self, device_name: str = "EAODV-Node", message_handler: Callable = None,
                 disconnection_handler: Callable = None):
        """Initialize the Bluetooth communication handler"""
        self.device_name = device_name
        self.message_handler = message_handler or self._default_message_handler

        self.message_handler = message_handler or self._default_message_handler
        self.disconnection_handler = disconnection_handler or self._default_disconnection_handler

        # Create Bluez command runner
        self.bluez = BluezCommandRunner()

        # State tracking
        self.server_socket = None
        self.server_thread = None
        self.client_threads = {}
        self.connected_devices: Dict[str, socket.socket] = {}
        self.stopping = False
        self.is_server_running = False

        # Thread safety
        self.connection_locks: Dict[str, threading.Lock] = {}
        self.devices_lock = threading.RLock()
        self.scan_lock = threading.Lock()

        # Buffer for incoming data
        self.receive_buffers: Dict[str, str] = {}

        # Get and store local Bluetooth address
        self.local_bt_address = self.bluez.get_local_address()
        if self.local_bt_address:
            logger.info(f"Local Bluetooth address: {self.local_bt_address}")

        # Start background scan and configure adapter
        self.bluez.start_scan()
        self.bluez.configure_adapter(self.device_name)

    def should_initiate_connection(self, remote_addr):
        """
        Determine if this device should initiate the connection
        based on Bluetooth address comparison
        """
        if not self.local_bt_address:
            return True
        return self.local_bt_address.lower() < remote_addr.lower()

    def get_connection_lock(self, addr: str) -> threading.Lock:
        """Get or create a mutex lock for a specific address"""
        if addr not in self.connection_locks:
            self.connection_locks[addr] = threading.Lock()
        return self.connection_locks[addr]

    def start_server(self):
        """Start server functionality - listening for connections"""
        if self.is_server_running:
            logger.info("Server already running")
            return True

        try:
            # Create a dedicated server socket
            self.server_socket = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Bind to port
            try:
                self.server_socket.bind(("", RFCOMM_PORT))
            except Exception as e:
                logger.error(f"Failed to bind to port {RFCOMM_PORT}: {e}")
                logger.info("Trying alternative port")
                alt_port = 1
                self.server_socket.bind(("", alt_port))
                logger.info(f"Bound to alternative port {alt_port}")

            self.server_socket.listen(5)

            # Advertise the service
            bluetooth.advertise_service(
                self.server_socket,
                f"E-AODV {self.device_name}",
                service_id=EAODV_UUID,
                service_classes=[EAODV_UUID, bluetooth.SERIAL_PORT_CLASS],
                profiles=[bluetooth.SERIAL_PORT_PROFILE],
                protocols=[bluetooth.RFCOMM_UUID]
            )

            logger.info(f"Server listening on RFCOMM")
            self.is_server_running = True

            # Start server thread
            self.stopping = False
            self.server_thread = threading.Thread(target=self._accept_connections)
            self.server_thread.daemon = True
            self.server_thread.start()

            return True

        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            if self.server_socket:
                try:
                    self.server_socket.close()
                except:
                    pass
                self.server_socket = None
            return False

    def start_advertising(self):
        """Start advertising (wrapper for compatibility)"""
        return self.start_server()

    def _accept_connections(self):
        """Accept incoming Bluetooth connections (runs in a thread)"""
        logger.info("Server thread started, waiting for connections")

        while not self.stopping:
            try:
                # Accept connection - no timeout
                client_sock, client_info = self.server_socket.accept()
                bt_addr = client_info[0]
                logger.info(f"Accepted connection from {bt_addr}")

                # Check if this client should be connecting to us based on address comparison
                should_we_connect = self.should_initiate_connection(bt_addr)

                if should_we_connect:
                    # We should initiate connections to them, not accept theirs
                    logger.info(f"Rejecting connection from {bt_addr} - we should initiate instead")
                    client_sock.close()
                else:
                    # They should initiate connections to us, which is what's happening
                    connection_lock = self.get_connection_lock(bt_addr)
                    if connection_lock.acquire(timeout=2.0):
                        try:
                            with self.devices_lock:
                                if bt_addr in self.connected_devices:
                                    logger.info(f"Replacing existing connection from {bt_addr}")
                                    try:
                                        self.connected_devices[bt_addr].close()
                                    except:
                                        pass

                            # Give the connection time to settle before proceeding
                            time.sleep(1.0)

                            # Create a thread to handle this connection
                            self._handle_new_connection(client_sock, bt_addr)
                        finally:
                            connection_lock.release()
                    else:
                        logger.warning(f"Could not acquire lock for {bt_addr}, rejecting connection")
                        client_sock.close()

            except Exception:
                # No error logging as requested
                if self.stopping:
                    break
                time.sleep(0.1)

        logger.info("Server thread stopped")

    def _handle_new_connection(self, sock: bluetooth.BluetoothSocket, addr: str):
        """Handle new client connection in a thread"""
        # Set socket options for better stability
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except:
            pass

        # Store the connection
        with self.devices_lock:
            self.connected_devices[addr] = sock
            self.receive_buffers[addr] = ""

        # Create a thread to handle communication
        thread = threading.Thread(
            target=self._handle_client_communication,
            args=(sock, addr)
        )
        thread.daemon = True
        thread.start()

        # Store the thread reference
        self.client_threads[addr] = thread

    def _is_socket_connected(self, sock):
        """Check if a socket is still connected"""
        try:
            # Try to get peer name - will fail if socket is disconnected
            sock.getpeername()
            return True
        except:
            return False

    def _handle_client_communication(self, sock: bluetooth.BluetoothSocket, addr: str):
        """Handle communication with a client (runs in a thread)"""
        logger.info(f"Starting communication handler for {addr}")

        # Give connection time to stabilize before any action
        time.sleep(3.0)

        # First just check if socket is still alive
        if not self._is_socket_connected(sock):
            logger.error(f"Connection to {addr} failed socket check")
            self._remove_connection(addr)
            return

        logger.info(f"Connection to {addr} is stable, proceeding")

        # Try to send hello message
        try:
            hello_msg = {
                "type": "hello",
                "source": self.device_name,
                "timestamp": time.time()
            }
            hello_bytes = (json.dumps(hello_msg) + "\n").encode("utf-8")
            logger.info(f"Sending hello message to {addr}")
            sock.send(hello_bytes)
            logger.info(f"Successfully sent hello to {addr}")
        except Exception as e:
            logger.error(f"Failed to send hello message to {addr}: {e}")
            self._remove_connection(addr)
            return

        # Main receive loop
        disconnect_reason = "Normal closure"
        try:
            while not self.stopping:
                # ... existing code ...

                try:
                    # Non-blocking socket check
                    if not self._is_socket_connected(sock):
                        logger.info(f"Socket to {addr} disconnected")
                        disconnect_reason = "Socket disconnected"
                        break

                    # Blocking receive - no timeout
                    data = sock.recv(BUFFER_SIZE)
                    if not data:
                        logger.info(f"Client {addr} disconnected")
                        disconnect_reason = "Connection closed by peer"
                        break

                    self._process_received_data(addr, data)

                except Exception as e:
                    logger.error(f"Socket error with {addr}: {e}")
                    disconnect_reason = f"Socket error: {str(e)}"
                    break
        finally:
            self._remove_connection(addr, disconnect_reason)

    def _process_received_data(self, sender_addr: str, data: bytes):
        """Process received data"""
        try:
            decoded_data = data.decode('utf-8')

            with self.devices_lock:
                if sender_addr not in self.receive_buffers:
                    self.receive_buffers[sender_addr] = ""
                self.receive_buffers[sender_addr] += decoded_data
                buffer = self.receive_buffers[sender_addr]

            # Parse for complete JSON objects
            depth = 0
            start_idx = None
            processed_up_to = 0

            for i, char in enumerate(buffer):
                if char == '{':
                    if depth == 0:
                        start_idx = i
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        # Found a complete JSON object
                        json_str = buffer[start_idx:i + 1]
                        try:
                            message_data = json.loads(json_str)
                            self.message_handler(sender_addr, message_data)
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid JSON received: {json_str}")

                        processed_up_to = i + 1

            # Store remaining data
            with self.devices_lock:
                if sender_addr in self.receive_buffers:
                    self.receive_buffers[sender_addr] = buffer[processed_up_to:]

        except Exception as e:
            logger.error(f"Error processing data from {sender_addr}: {e}")

    def _default_disconnection_handler(self, addr: str, reason: str):
        """Default handler just logs the disconnection"""
        logger.info(f"Disconnection from {addr}: {reason}")

    def _default_message_handler(self, sender_address: str, message_data: Dict[str, Any]):
        """Default handler just logs the received message"""
        logger.info(f"Received message from {sender_address}: {message_data}")

    def _remove_connection(self, addr: str, reason: str = "Unknown"):
        """Remove a connection and clean up resources"""
        connection_lock = self.get_connection_lock(addr)
        if connection_lock.acquire(timeout=2.0):
            try:
                with self.devices_lock:
                    if addr in self.connected_devices:
                        try:
                            sock = self.connected_devices[addr]
                            # Proper socket shutdown sequence
                            try:
                                sock.shutdown(socket.SHUT_RDWR)
                            except:
                                pass
                            sock.close()
                        except:
                            pass
                        del self.connected_devices[addr]

                    if addr in self.client_threads:
                        del self.client_threads[addr]

                    if addr in self.receive_buffers:
                        del self.receive_buffers[addr]

                logger.info(f"Removed connection to {addr}")

                # Notify protocol layer about disconnection
                if self.disconnection_handler:
                    self.disconnection_handler(addr, reason)

            finally:
                connection_lock.release()
        else:
            logger.warning(f"Could not acquire lock to remove connection to {addr}")

    def scan_for_nodes(self, duration: int = 8) -> List[Tuple[str, str]]:
        """Scan for other E-AODV nodes"""
        if not self.scan_lock.acquire(blocking=False):
            logger.info("Scan already in progress, skipping")
            return []

        try:
            logger.info(f"Scanning for E-AODV devices (timeout: {duration}s)...")

            nearby_devices = self.bluez.discover_devices(duration=duration)
            found_devices = []

            for addr, name in nearby_devices:
                name = name or "Unknown"
                logger.info(f"Found device: {name} ({addr})")
                if "EAODV" in name or "Rasp" in name:
                    found_devices.append((addr, name))
                    logger.info(f"  --> Potential E-AODV device!")

            logger.info(f"Found {len(found_devices)} potential E-AODV devices")
            return found_devices

        finally:
            self.scan_lock.release()

    def connect_to_node_with_backoff(self, addr: str, name: str, max_retries: int = MAX_CONNECT_RETRIES) -> bool:
        """
        Connect to an E-AODV node with binary exponential backoff.

        Args:
            addr: Bluetooth MAC address
            name: Device name
            max_retries: Maximum number of retry attempts

        Returns:
            True if connection was successful, False otherwise
        """
        # Check if we should initiate connection
        if not self.should_initiate_connection(addr):
            logger.info(f"Not initiating connection to {name} ({addr}) - they should connect to us")
            return False

        # Check if already connected
        with self.devices_lock:
            if addr in self.connected_devices:
                logger.info(f"Already connected to {name} ({addr})")
                return True

        # Get connection lock
        connection_lock = self.get_connection_lock(addr)
        if not connection_lock.acquire(timeout=0.5):
            logger.info(f"Another connection attempt to {addr} is in progress, skipping")
            return False

        try:
            # Check if paired
            paired_devices = self.bluez.get_paired_devices()
            if addr not in paired_devices:
                logger.error(f"Device {addr} is not paired - manual pairing required")
                return False

            logger.info(f"Device {addr} is paired, beginning connection attempts with backoff")

            retry_count = 0
            success = False

            while retry_count < max_retries and not success:
                try:
                    # Calculate and apply backoff delay
                    delay = calculate_backoff_delay(retry_count)
                    if delay > 0:
                        logger.info(f"Retry #{retry_count} for {addr} - waiting {delay:.2f}s before next attempt")
                        time.sleep(delay)

                    # Create socket without timeout
                    client_sock = bluetooth.BluetoothSocket(bluetooth.RFCOMM)
                    client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                    # Try connecting
                    logger.info(f"Connecting to {addr} on port {RFCOMM_PORT} (attempt #{retry_count + 1})")
                    client_sock.connect((addr, RFCOMM_PORT))
                    logger.info(f"Connected to {name} ({addr}) on port {RFCOMM_PORT}")

                    # Connection successful - proceed with setup
                    # Allow connection to stabilize
                    time.sleep(1.0)

                    # Set up connection handling
                    with self.devices_lock:
                        self.connected_devices[addr] = client_sock
                        self.receive_buffers[addr] = ""

                    # Start a thread to handle communication
                    thread = threading.Thread(
                        target=self._handle_client_communication,
                        args=(client_sock, addr)
                    )
                    thread.daemon = True
                    thread.start()

                    with self.devices_lock:
                        self.client_threads[addr] = thread

                    success = True

                except Exception as e:
                    retry_count += 1
                    logger.warning(f"Connection attempt #{retry_count} to {addr} failed: {e}")

                    if retry_count >= max_retries:
                        logger.error(f"Failed to connect to {addr} after {max_retries} attempts")

                    # Make sure socket is properly closed if it was created
                    try:
                        client_sock.close()
                    except:
                        pass

            return success

        finally:
            connection_lock.release()

    def connect_to_node(self, addr: str, name: str) -> bool:
        """
        Connect to another E-AODV node (wrapper method using backoff)

        Args:
            addr: Bluetooth MAC address
            name: Device name

        Returns:
            True if connection was successful, False otherwise
        """
        return self.connect_to_node_with_backoff(addr, name)

    def send_json(self, addr: str, data: Dict[str, Any]) -> bool:
        """Send JSON data to a connected device"""
        sock = None
        with self.devices_lock:
            if addr not in self.connected_devices:
                logger.error(f"Not connected to {addr}")
                return False
            sock = self.connected_devices[addr]

        # Check if socket is actually connected
        if not self._is_socket_connected(sock):
            logger.error(f"Socket to {addr} is disconnected")
            self._remove_connection(addr)
            return False

        try:
            # Send the JSON data
            json_str = json.dumps(data) + "\n"
            json_bytes = json_str.encode('utf-8')
            sock.send(json_bytes)
            logger.info(f"Sent JSON message to {addr}")
            return True

        except Exception as e:
            logger.error(f"Failed to send message to {addr}: {e}")
            self._remove_connection(addr)
            return False

    def disconnect(self, addr: str) -> bool:
        """Disconnect from a specific device"""
        if addr in self.connected_devices:
            self._remove_connection(addr)
            return True
        return False

    def close(self):
        """Clean up all resources"""
        logger.info("Shutting down Bluetooth communication")
        self.stopping = True

        # Close all client connections
        with self.devices_lock:
            devices_to_disconnect = list(self.connected_devices.keys())

        for addr in devices_to_disconnect:
            self.disconnect(addr)

        # Stop server
        if self.is_server_running and self.server_socket:
            try:
                bluetooth.stop_advertising(self.server_socket)
            except:
                pass

            try:
                self.server_socket.close()
            except:
                pass

            self.server_socket = None
            self.is_server_running = False

        # Wait for server thread to end
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(2.0)

        # Stop background scanning
        self.bluez.stop_scan()

        logger.info("Bluetooth communication shutdown complete")