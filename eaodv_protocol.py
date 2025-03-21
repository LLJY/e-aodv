#!/usr/bin/env python3
"""
E-AODV Protocol Implementation

This module implements the Enhanced AODV protocol for Bluetooth Low Energy networks.
It integrates the Bluetooth communication layer with EAODV protocol data models.
"""
import time
import random
import logging
import threading
import uuid
import json
import subprocess
import os
from typing import Dict, List, Optional, Tuple, Callable, Set, Any
from sensors.sensor_registry import SensorRegistry
from sensors.cpu_temperature import CPUTemperatureSensor
from dataclasses import asdict

# Import Bluetooth communication module
from bt_communication import BTCommunication, BluezCommandRunner, MAX_CONNECT_RETRIES

# Import EAODV data models
from e_aodv_models import (
    RequestType, OperationType, TopologyEntry, E_RREQ, E_RREP, E_RERR, R_HELLO,
    RoutingTableEntry, AodvState, NetworkConfig, to_json, from_json
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_TTL = 10  # Default time-to-live for route requests
ROUTE_DISCOVERY_TIMEOUT = 10  # Timeout for route discovery in seconds
ROUTE_CACHE_TIMEOUT = 300  # Route cache timeout in seconds (5 minutes)
HELLO_INTERVAL = 30  # Interval between hello messages in seconds (reduced from 60)
DISCOVERY_INTERVAL = 90  # Auto-discovery interval in seconds
AUTO_CONNECT_INTERVAL = 20  # Interval for connection attempts in seconds


class EAODVProtocol:
    """
    Enhanced AODV Protocol Implementation

    This class implements the E-AODV protocol, providing routing functionality
    for Bluetooth nodes in a mesh network.
    """

    def __init__(self, node_id: str, auto_discovery: bool = True, capabilities: Optional[Dict[str, Any]] = None,
                 sensor_registry: Optional[SensorRegistry] = None):
        """
        Initialize the E-AODV protocol.

        Args:
            node_id: A human-readable identifier for this node
            auto_discovery: Whether to automatically discover and connect to nodes
            capabilities: Dictionary of node capabilities (e.g., temperature, humidity, motor)
            sensor_registry: Optional sensor registry object, will create one if not provided
        """
        self.node_id = node_id
        self.device_name = f"EAODV-{node_id}"
        self.auto_discovery = auto_discovery
        self.broadcasts = []
        self.node_capabilities = {}

        # Initialize raw capabilities (will be updated with sensor data)
        self.capabilities = capabilities or {}

        # Initialize or use provided sensor registry
        if sensor_registry is None:
            self.sensor_registry = SensorRegistry()
            # Initialize sensors based on declared capabilities
            self._init_sensors_from_capabilities()
        else:
            self.sensor_registry = sensor_registry

        # Update capabilities from the sensor registry to ensure consistency
        self.capabilities.update(self.sensor_registry.get_all_capabilities())

        # Initialize network configuration
        self.network_config = NetworkConfig()

        # Initialize Bluetooth communication
        self.bt_comm = BTCommunication(
            device_name=self.device_name,
            message_handler=self._handle_message,
            disconnection_handler=self._handle_disconnection,
            hello_message_generator=self._create_hello_message
        )

        # Get local MAC address
        self.mac_address = self.bt_comm.local_bt_address
        if self.mac_address:
            # Just take the first 17 characters which is the MAC address format (XX:XX:XX:XX:XX:XX)
            self.mac_address = self.mac_address[0:17]
        else:
            logger.error("Could not get local Bluetooth MAC address")
            self.mac_address = str(uuid.uuid4())  # Fallback to a UUID

        logger.info(f"Node initialized: {self.node_id} ({self.mac_address})")
        logger.info(f"Node capabilities: {self.capabilities}")

        # Initialize AODV state
        self.aodv_state = AodvState(
            mac_address=self.mac_address,
            sequence_number=1,
            neighbours=[],
            routing_table=[]
        )

        # Store neighbor capabilities
        self.neighbor_capabilities: Dict[str, Dict[str, Any]] = {}

        # Pending query callbacks
        self.pending_queries: Dict[str, Callable] = {}  # broadcast_id -> callback

        # Route discovery state tracking
        self.pending_route_requests: Dict[str, Dict] = {}  # broadcast_id -> request info
        self.processed_route_requests: Set[str] = set()  # Set of seen broadcast_ids
        self.route_discovery_callbacks: Dict[str, Callable] = {}  # dest_mac -> callback

        # Discovered but not connected nodes
        self.discovered_nodes: Dict[str, Dict[str, Any]] = {}  # addr -> {name, last_seen}

        # Thread safety
        self.state_lock = threading.RLock()
        self.discovery_lock = threading.Lock()
        self.query_lock = threading.RLock()

        # Start background tasks
        self._init_background_tasks()

    def _init_sensors_from_capabilities(self):
        """Initialize sensors based on declared capabilities"""
        # Map of capability name to sensor class name and module
        sensor_mapping = {
            "temperature": ("CPUTemperatureSensor", "sensors.cpu_temperature"),
            "humidity": ("HumiditySensor", "sensors.humidity"),
            "motion": ("MotionSensor", "sensors.motion"),
            "led": ("LEDSensor", "sensors.led"),
            "motor": ("MotorSensor", "sensors.motor"),
            "display": ("DisplaySensor", "sensors.display"),
        }

        # Initialize each sensor based on capabilities
        for capability, (class_name, module_path) in sensor_mapping.items():
            if self.capabilities.get(capability, False):
                try:
                    # Import the module dynamically
                    module = __import__(module_path, fromlist=[class_name])
                    # Get the sensor class
                    sensor_class = getattr(module, class_name)
                    # Instantiate the sensor
                    sensor = sensor_class(enabled=True, simulate=True)
                    # Register it
                    self.sensor_registry.register_sensor(sensor)
                    logger.info(f"Initialized {capability} sensor")
                except ImportError:
                    logger.warning(f"Could not import {module_path}.{class_name}, skipping {capability}")
                    self.capabilities.pop(capability, None)
                except Exception as e:
                    logger.warning(f"Failed to initialize {capability} sensor: {e}")
                    self.capabilities.pop(capability, None)

    def _init_background_tasks(self):
        """Initialize background maintenance tasks"""
        # Start server functionality
        self.bt_comm.start_server()

        # Start periodic hello messages
        self.hello_thread = threading.Thread(target=self._periodic_hello_task)
        self.hello_thread.daemon = True
        self.hello_thread.start()

        # Start route maintenance thread
        self.maintenance_thread = threading.Thread(target=self._route_maintenance_task)
        self.maintenance_thread.daemon = True
        self.maintenance_thread.start()

        # Start auto-discovery if enabled
        if self.auto_discovery:
            self.discovery_thread = threading.Thread(target=self._auto_discovery_task)
            self.discovery_thread.daemon = True
            self.discovery_thread.start()

            self.connect_thread = threading.Thread(target=self._auto_connect_task)
            self.connect_thread.daemon = True
            self.connect_thread.start()

    def _periodic_hello_task(self):
        """Periodically send hello messages to maintain neighborhood information"""
        while True:
            try:
                # Wait for initial startup delay
                time.sleep(random.uniform(2, 5))

                # Create and broadcast a hello message
                self._broadcast_hello()

                # Get current hello interval from network configuration
                hello_interval = self.network_config.hello_interval

                # Wait for the configured interval with some randomization to avoid synchronization
                time.sleep(hello_interval + random.uniform(-5, 5))

            except Exception as e:
                logger.error(f"Error in hello task: {e}")
                time.sleep(5)  # Shorter retry on error

    def _auto_discovery_task(self):
        """Periodically discover nearby nodes automatically"""
        # Initial delay to let system stabilize
        time.sleep(random.uniform(5, 10))

        while True:
            try:
                logger.info("Auto-discovery: Scanning for nearby nodes...")

                # Scan for nodes
                devices = self.discover_neighbors(duration=5)  # Shorter duration for auto-discovery

                # Store discovered devices
                with self.discovery_lock:
                    for addr, name in devices:
                        # Skip if already connected
                        with self.state_lock:
                            already_connected = False
                            for neighbor in self.aodv_state.neighbours:
                                if neighbor.bt_mac_address == addr:
                                    already_connected = True
                                    break

                        if not already_connected and ("EAODV" in name or "Rasp" in name):
                            self.discovered_nodes[addr] = {
                                "name": name,
                                "last_seen": time.time()
                            }
                            logger.info(f"Auto-discovery: Found node {name} ({addr})")

                # Wait for the next interval
                time.sleep(DISCOVERY_INTERVAL + random.uniform(-5, 10))

            except Exception as e:
                logger.error(f"Error in auto-discovery task: {e}")
                time.sleep(30)  # Longer retry on error

    def _auto_connect_task(self):
        """Periodically try to connect to discovered nodes"""
        # Initial delay to let discovery happen first
        time.sleep(random.uniform(15, 20))

        while True:
            try:
                # Get discovered but not connected nodes
                connect_candidates = []

                with self.discovery_lock:
                    # Get a copy of discovered nodes for connection attempts
                    for addr, info in self.discovered_nodes.items():
                        # Skip if already connected
                        with self.state_lock:
                            already_connected = False
                            for neighbor in self.aodv_state.neighbours:
                                if neighbor.bt_mac_address == addr:
                                    already_connected = True
                                    break

                        if not already_connected:
                            connect_candidates.append((addr, info["name"]))

                # Try to connect to each candidate using shared backoff mechanism
                for addr, name in connect_candidates:
                    logger.info(f"Auto-connect: Attempting to connect to {name} ({addr})")
                    success = self.bt_comm.connect_to_node_with_backoff(addr, name, max_retries=MAX_CONNECT_RETRIES)

                    if success:
                        logger.info(f"Auto-connect: Successfully connected to {name} ({addr})")
                    else:
                        logger.info(f"Auto-connect: Failed to connect to {name} ({addr})")

                    # Short delay between connection attempt sequences
                    time.sleep(0.5)

                # Wait for the next interval (with some randomization)
                wait_time = AUTO_CONNECT_INTERVAL + random.uniform(-2, 5)
                logger.debug(f"Auto-connect: Next connection attempts in {wait_time:.1f}s")
                time.sleep(wait_time)

            except Exception as e:
                logger.error(f"Error in auto-connect task: {e}")
                time.sleep(15)  # Retry on error

    def _route_maintenance_task(self):
        """Maintain routes, clean up expired entries"""
        while True:
            try:
                time.sleep(30)  # Check every 30 seconds

                with self.state_lock:
                    # Clean up pending route requests
                    now = time.time()
                    expired_requests = []

                    for broadcast_id, request_info in self.pending_route_requests.items():
                        if now - request_info["timestamp"] > ROUTE_DISCOVERY_TIMEOUT:
                            expired_requests.append(broadcast_id)
                            dest_mac = request_info.get("dest_mac")
                            callback = self.route_discovery_callbacks.pop(dest_mac, None)
                            if callback:
                                try:
                                    callback(False, None)
                                except Exception as e:
                                    logger.error(f"Error calling route discovery callback: {e}")

                    # Remove expired requests
                    for broadcast_id in expired_requests:
                        self.pending_route_requests.pop(broadcast_id, None)

                    # Clean up pending queries
                    with self.query_lock:
                        expired_queries = []
                        for broadcast_id in self.pending_queries:
                            if broadcast_id in expired_requests:
                                expired_queries.append(broadcast_id)

                        for broadcast_id in expired_queries:
                            callback = self.pending_queries.pop(broadcast_id, None)
                            if callback:
                                try:
                                    callback(False, {"status": "error", "message": "Query timed out"})
                                except Exception as e:
                                    logger.error(f"Error calling query callback: {e}")

                    # Limit size of processed requests cache
                    if len(self.processed_route_requests) > 1000:
                        self.processed_route_requests.clear()

            except Exception as e:
                logger.error(f"Error in route maintenance task: {e}")
                time.sleep(5)

    def _create_hello_message(self) -> Dict[str, Any]:
        """
        Create a standardized R_HELLO message with enhanced topology information.

        Returns:
            Dictionary containing the R_HELLO message data
        """
        # Get current neighbor MACs
        with self.state_lock:
            neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

            # Create simplified routing knowledge to share
            # When creating the routing_knowledge in _create_hello_message
            routing_knowledge = []
            for route in self.aodv_state.routing_table:
                # Only share routes with reasonable hop counts
                if len(route.hops) <= 3:  # Limit to routes with 3 or fewer hops
                    hop_list = []
                    for hop in route.hops:
                        hop_info = {
                            "node_id": hop.node_id,
                            "bt_mac_address": hop.bt_mac_address,
                            "neighbors": hop.neighbors
                        }

                        # Add capability information if available
                        hop_capabilities = self.node_capabilities.get(hop.bt_mac_address, {})
                        if hop_capabilities:
                            hop_info["capabilities"] = hop_capabilities

                        hop_list.append(hop_info)

                    # Add destination with capabilities
                    # Always include capabilities for all routes regardless of hop count
                    dest_info = {
                        "node_id": route.host.node_id,
                        "bt_mac_address": route.host.bt_mac_address
                    }

                    # Add capability information for destination - prioritize this part
                    dest_capabilities = self.node_capabilities.get(route.host.bt_mac_address, {})
                    if dest_capabilities:
                        dest_info["capabilities"] = dest_capabilities
                        logger.debug(
                            f"Including capabilities for {route.host.node_id or route.host.bt_mac_address} in RHELLO")

                    routing_knowledge.append({
                        "destination": dest_info,
                        "hops": hop_list,
                        "timestamp": str(time.time())
                    })

        # Get updated capabilities from sensor registry
        capability_data = self.sensor_registry.get_all_capabilities()

        # Check if we should include sensor data (based on network config)
        include_sensor_data = self.network_config.include_sensor_data

        # Create hello message
        hello_msg = {
            "type": RequestType.R_HELLO.value,
            "node_id": self.node_id,
            "bt_mac_address": self.mac_address,
            "timestamp": str(time.time()),
            "initial_neighbors": neighbor_macs,
            "routing_knowledge": routing_knowledge,  # Add routing knowledge
            "capabilities": capability_data,
            "hello_interval": self.network_config.hello_interval,
            "include_sensor_data": include_sensor_data
        }

        # Add sensor data if configured to do so
        if include_sensor_data:
            # Read all sensor values
            sensor_data = self.sensor_registry.read_all_sensors()
            if sensor_data:
                hello_msg["sensor_data"] = sensor_data

        return hello_msg

    def _broadcast_hello(self):
        """Broadcast a hello message to neighbors"""
        try:
            # Create hello message using the shared function
            hello_json = self._create_hello_message()

            # Broadcast to all connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            for device_addr in connected_devices:
                try:
                    self.bt_comm.send_json(device_addr, hello_json)
                except Exception as e:
                    logger.error(f"Failed to send hello to {device_addr}: {e}")

            logger.info(f"Broadcast hello message to {len(connected_devices)} devices")

        except Exception as e:
            logger.error(f"Error broadcasting hello: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_message(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Handle received messages according to type.

        This function is called by the Bluetooth communication layer when
        a message is received from another node.

        Args:
            sender_address: MAC address of the sender
            message_data: Decoded JSON message
        """
        try:
            msg_type = message_data.get("type")

            # Update sender as a neighbor
            self._update_neighbor(sender_address, message_data)

            broadcast_id = message_data.get("broadcast_id", None)
            ttl = message_data.get("time_to_live", None)

            if broadcast_id is not None and broadcast_id in self.broadcasts:
                # do nothing and drop the packet as we have seen this broadcast before.
                logger.info(f"we have seen this packet before... dropping... broadcast id: {broadcast_id}")
                return
            if ttl is not None and int(ttl) == -1:
                logger.info(f"dropping TTL packet!")
                # do nothing and drop this packet as it has reached TTL
                return
            if broadcast_id is not None:
                self.broadcasts.append(broadcast_id)

            # Process message based on type
            if msg_type == RequestType.E_RREQ.value:
                self._handle_route_request(sender_address, message_data)
            elif msg_type == RequestType.E_RREP.value:
                self._handle_route_reply(sender_address, message_data)
            elif msg_type == RequestType.E_RERR.value:
                self._handle_route_error(sender_address, message_data)
            elif msg_type == RequestType.R_HELLO.value:
                self._handle_hello(sender_address, message_data)
            else:
                logger.warning(f"Received unknown message type: {msg_type}")

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def _update_neighbor(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Update neighbor information based on received message.

        Args:
            sender_address: MAC address of the sender
            message_data: Message data
        """
        try:
            # Extract sender information
            node_id = message_data.get("node_id") or message_data.get("source_id")
            neighbors = []

            # If this is a hello message, it might contain neighbor information
            if message_data.get("type") == RequestType.R_HELLO.value:
                neighbors = message_data.get("initial_neighbors", [])

            # Update neighbor in AODV state
            if node_id:
                with self.state_lock:
                    self.aodv_state.add_neighbor(
                        neighbor_mac=sender_address,
                        neighbor_id=node_id,
                        neighbors=neighbors
                    )

                    # Update neighbor capabilities if included
                    if "capabilities" in message_data:
                        self.neighbor_capabilities[sender_address] = message_data["capabilities"]

                    logger.debug(f"Updated neighbor: {node_id} ({sender_address})")

        except Exception as e:
            logger.error(f"Error updating neighbor: {e}")

    def _handle_disconnection(self, addr: str, reason: str):
        """
        Handle disconnection events from the BT communication layer.

        Args:
            addr: MAC address of the disconnected node
            reason: Reason for disconnection
        """
        try:
            logger.info(f"Handling disconnection from {addr}: {reason}")

            # Check if this is one of our neighbors
            with self.state_lock:
                is_neighbor = any(n.bt_mac_address == addr for n in self.aodv_state.neighbours)
                neighbor = next((n for n in self.aodv_state.neighbours if n.bt_mac_address == addr), None)

            if not is_neighbor:
                logger.info(f"Disconnected node {addr} was not a neighbor")
                return

            # Define error strings as lowercase to avoid repeated conversions
            reconnect_errors = ["connection reset by peer", "broken pipe", "timed out"]
            # Convert reason to lowercase once
            reason_lower = reason.lower()
            # Check if any error strings appear in the lowercase reason
            should_try_reconnect = any(err in reason_lower for err in reconnect_errors)

            if should_try_reconnect:
                logger.info(f"Attempting to reconnect to {addr}")
                node_name = neighbor.node_id if neighbor and neighbor.node_id else f"EAODV-{addr[-5:]}"

                # Try reconnection with backoff
                for attempt in range(2):  # Only try twice to avoid long delays
                    time.sleep(1 + attempt)  # Simple backoff
                    if self.bt_comm.connect_to_node(addr, node_name):
                        logger.info(f"Successfully reconnected to {addr}")
                        return

                logger.info(f"Failed to reconnect to {addr} after 2 attempts")

            # Generate E-RERR message
            e_rerr = E_RERR()
            node_id = neighbor.node_id if neighbor else ""
            e_rerr.prepare_error(
                failed_node_id=node_id,
                failed_node_mac=addr,
                originator_id=self.node_id,
                originator_mac=self.mac_address,
                reason=reason
            )

            # Update our own routing table and neighbor list
            with self.state_lock:
                # Remove routes that use this node
                self._remove_routes_through_node(addr)

                # Remove from neighbors list
                self.aodv_state.neighbours = [n for n in self.aodv_state.neighbours
                                              if n.bt_mac_address != addr]

                # Remove from neighbor capabilities
                if addr in self.neighbor_capabilities:
                    del self.neighbor_capabilities[addr]

            # Broadcast E-RERR to all remaining neighbors
            error_data = {
                "type": RequestType.E_RERR.value,
                "failed_node_id": e_rerr.failed_node_id,
                "failed_node_mac": e_rerr.failed_node_mac,
                "originator_id": e_rerr.originator_id,
                "originator_mac": e_rerr.originator_mac,
                "timestamp": e_rerr.timestamp,
                "reason": e_rerr.reason
            }

            # Get all connected devices and send the error
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            for device_addr in connected_devices:
                try:
                    self.bt_comm.send_json(device_addr, error_data)
                    logger.info(f"Sent E-RERR about {addr} to {device_addr}")
                except Exception as e:
                    logger.error(f"Failed to send E-RERR to {device_addr}: {e}")

            logger.info(f"Completed disconnection handling for {addr}")

        except Exception as e:
            logger.error(f"Error handling disconnection: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_route_request(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Handle an incoming route request (E-RREQ).

        Args:
            sender_address: MAC address of the sender
            message_data: Decoded E-RREQ message
        """
        try:
            # Convert to E_RREQ object
            e_rreq = E_RREQ(
                src_id=message_data.get("source_id", ""),
                src_mac=message_data.get("source_mac", ""),
                dest_id=message_data.get("destination_id", ""),
                dest_mac=message_data.get("destination_mac", ""),
                sequence_number=message_data.get("sequence_number", 0),
                time_to_live=message_data.get("time_to_live", 0)
            )

            # Copy additional fields
            e_rreq.broadcast_id = message_data.get("broadcast_id", "")
            e_rreq.hop_count = message_data.get("hop_count", 0)
            e_rreq.timestamp = message_data.get("timestamp", "")
            e_rreq.previous_hop_mac = message_data.get("previous_hop_mac", "")
            e_rreq.operation_type = message_data.get("operation_type", OperationType.ROUTE.value)
            e_rreq.query_params = message_data.get("query_params", {})

            # Copy packet topology if available
            topology_data = message_data.get("packet_topology", [])
            e_rreq.packet_topology = []
            for entry in topology_data:
                if isinstance(entry, dict):
                    e_rreq.packet_topology.append(TopologyEntry(
                        node_id=entry.get("node_id"),
                        bt_mac_address=entry.get("bt_mac_address", ""),
                        neighbors=entry.get("neighbors", [])
                    ))

            # Check if we've already processed this request
            with self.state_lock:
                if e_rreq.broadcast_id in self.processed_route_requests:
                    logger.debug(f"Ignoring duplicate E-RREQ: {e_rreq.broadcast_id}")
                    return

                # Mark as processed
                self.processed_route_requests.add(e_rreq.broadcast_id)
            logger.info(
                f"Checking if E-RREQ is destined for us from {e_rreq.source_id}, destination: {e_rreq.destination_mac} {e_rreq.destination_id} Our Address: {self.mac_address}, comparison = {e_rreq.destination_mac.lower().strip() == self.mac_address.lower().strip()}")
            # Check if we're the destination
            if e_rreq.destination_mac.lower().strip() == self.mac_address.lower().strip() or e_rreq.destination_id.lower() == self.node_id.lower():
                # Log based on operation type
                op_type = OperationType(e_rreq.operation_type).name if e_rreq.operation_type in [e.value for e in
                                                                                                 OperationType] else "UNKNOWN"
                logger.info(f"Received E-RREQ ({op_type}) destined for us from {e_rreq.source_id}")

                # Handle based on operation type
                if e_rreq.operation_type == OperationType.QUERY.value:
                    self._handle_query_request(e_rreq, sender_address)
                elif e_rreq.operation_type == OperationType.WRITE.value:
                    self._handle_write_request(e_rreq, sender_address)
                elif e_rreq.operation_type == OperationType.CONFIG.value:
                    self._handle_config_request(e_rreq, sender_address)
                else:  # Default to route discovery
                    self._generate_route_reply(e_rreq, sender_address)
                return

            # Check TTL
            if e_rreq.time_to_live <= 0:
                logger.debug(f"Dropping E-RREQ with expired TTL")
                return

            # Update route to source
            self._update_route_to_source(e_rreq)

            # Forward the request
            self._forward_route_request(e_rreq, sender_address)

        except Exception as e:
            logger.error(f"Error handling route request: {e}")

    def _update_route_to_source(self, e_rreq: E_RREQ):
        """
        Update route to the source node based on received E-RREQ.

        Args:
            e_rreq: Enhanced Route Request
        """
        try:
            # Build reverse path from packet topology
            if e_rreq.packet_topology:
                with self.state_lock:
                    # Create a copy of the topology and reverse it
                    hops = e_rreq.packet_topology.copy()
                    hops.reverse()

                    # Remove duplicate nodes and loops
                    cleaned_hops = []
                    seen_macs = set()

                    for hop in hops:
                        # Skip if this is our own MAC (would create a loop)
                        if hop.bt_mac_address == self.mac_address:
                            continue

                        # Skip if we've already seen this MAC (duplicate/loop)
                        if hop.bt_mac_address in seen_macs:
                            logger.warning(f"Removing duplicate node {hop.bt_mac_address} from route")
                            continue

                        # Add to cleaned list and track MAC
                        cleaned_hops.append(hop)
                        seen_macs.add(hop.bt_mac_address)

                    # Update the route with cleaned hop list
                    self.aodv_state.add_or_update_route(
                        dest_mac=e_rreq.source_mac,
                        dest_id=e_rreq.source_id,
                        hops=cleaned_hops
                    )

                    logger.debug(
                        f"Updated route to {e_rreq.source_id} ({e_rreq.source_mac}) with {len(cleaned_hops)} hops")
        except Exception as e:
            logger.error(f"Error updating route to source: {e}")

    def _forward_route_request(self, e_rreq: E_RREQ, sender_address: str):
        """
        Forward a route request to neighbors with enhanced topology information.

        Args:
            e_rreq: Enhanced Route Request
            sender_address: Address of the node that sent us this request
        """
        try:
            # Get current neighbors
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

                # Get selected routing knowledge to enhance topology
                routing_knowledge = []
                # Focus on short routes (1-2 hops) to enrich topology information
                for route in self.aodv_state.routing_table:
                    if len(route.hops) <= 2:  # Only include short routes
                        dest = route.host

                        # Skip routes to the request source or destination
                        if dest.bt_mac_address in [e_rreq.source_mac, e_rreq.destination_mac]:
                            continue

                        hop_list = []
                        for hop in route.hops:
                            hop_list.append({
                                "node_id": hop.node_id,
                                "bt_mac_address": hop.bt_mac_address,
                                "neighbors": hop.neighbors
                            })

                        routing_knowledge.append({
                            "destination": {
                                "node_id": dest.node_id,
                                "bt_mac_address": dest.bt_mac_address
                            },
                            "hops": hop_list,
                            "timestamp": str(time.time())
                        })

                        # Limit to 5 routes to avoid excessive packet size
                        if len(routing_knowledge) >= 5:
                            break

            # Prepare the request for forwarding with enhanced topology
            e_rreq.forward_prepare(
                current_node_mac=self.mac_address,
                current_node_id=self.node_id,
                neighbors=neighbor_macs,
                routing_knowledge=routing_knowledge
            )

            # Convert to JSON - FIX: properly handle TopologyEntry serialization
            forward_data = {
                "type": RequestType.E_RREQ.value,
                "source_id": e_rreq.source_id,
                "source_mac": e_rreq.source_mac,
                "destination_id": e_rreq.destination_id,
                "destination_mac": e_rreq.destination_mac,
                "broadcast_id": e_rreq.broadcast_id,
                "sequence_number": e_rreq.sequence_number,
                "hop_count": e_rreq.hop_count,
                "time_to_live": e_rreq.time_to_live,
                "timestamp": e_rreq.timestamp,
                "previous_hop_mac": e_rreq.previous_hop_mac,
                "operation_type": e_rreq.operation_type,
                "query_params": e_rreq.query_params,
                "packet_topology": []  # Initialize as empty list
            }

            # FIX: Proper serialization of packet topology entries
            if e_rreq.packet_topology:
                topology_entries = []
                for entry in e_rreq.packet_topology:
                    # Safely convert each entry to dict
                    if hasattr(entry, 'asdict'):
                        topology_entries.append(asdict(entry))
                    elif hasattr(entry, '__dict__'):
                        # For non-dataclass objects with __dict__
                        topology_entries.append(entry.__dict__)
                    else:
                        # Entry is already a dict or primitive type
                        topology_entries.append({
                            "node_id": entry.node_id if hasattr(entry, "node_id") else None,
                            "bt_mac_address": entry.bt_mac_address if hasattr(entry, "bt_mac_address") else "",
                            "neighbors": entry.neighbors if hasattr(entry, "neighbors") else []
                        })
                forward_data["packet_topology"] = topology_entries
            # Get connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            # Forward to all neighbors except the sender
            forwarded = 0
            for device_addr in connected_devices:
                if device_addr != sender_address:
                    try:
                        self.bt_comm.send_json(device_addr, forward_data)
                        forwarded += 1
                    except Exception as e:
                        logger.error(f"Failed to forward E-RREQ to {device_addr}: {e}")

            # Log with operation type
            op_type = OperationType(e_rreq.operation_type).name if e_rreq.operation_type in [e.value for e in
                                                                                             OperationType] else "UNKNOWN"
            logger.info(f"Forwarded E-RREQ ({op_type}) to {forwarded} devices with enhanced topology information")

        except Exception as e:
            logger.error(f"Error forwarding route request: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _generate_route_reply(self, e_rreq: E_RREQ, sender_address: str):
        """
        Generate a route reply when we are the destination of a route request.
        Includes optimized topology information.

        Args:
            e_rreq: Enhanced Route Request
            sender_address: Address of the node that sent us the request
        """
        try:
            # Clean and optimize the packet topology at the destination
            # This ensures we only include useful topology information
            if e_rreq.packet_topology:
                # Create a dictionary representation for the optimize_topology function
                topology_entries = []
                for entry in e_rreq.packet_topology:
                    if hasattr(entry, 'bt_mac_address') and entry.bt_mac_address:
                        topology_entries.append({
                            "node_id": entry.node_id,
                            "bt_mac_address": entry.bt_mac_address,
                            "neighbors": entry.neighbors,
                            "timestamp": e_rreq.timestamp  # Use request timestamp
                        })

                # Optimize the topology information
                from topology_utils import optimize_topology  # Dynamically import
                optimized_entries = optimize_topology(topology_entries)

                # Rebuild the packet_topology with optimized data
                e_rreq.packet_topology = []
                for entry in optimized_entries:
                    e_rreq.packet_topology.append(TopologyEntry(
                        node_id=entry.get("node_id"),
                        bt_mac_address=entry.get("bt_mac_address", ""),
                        neighbors=entry.get("neighbors", [])
                    ))
                logger.info(f"Optimized topology information for reply: {len(optimized_entries)} entries")

            # Create route reply from the optimized request
            e_rrep = E_RREP.from_erreq(e_rreq)

            # Get neighbors
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

                # Enrich with up to 3 additional routes from our routing table
                # to help establish a better network map at the source
                additional_routes = 0
                if e_rreq.operation_type == OperationType.ROUTE.value:
                    # Only add extra routes for pure route discovery (not queries/writes)
                    for route in self.aodv_state.routing_table:
                        # Skip routes to the source/destination nodes
                        if (route.host.bt_mac_address == e_rreq.source_mac or
                                route.host.bt_mac_address == e_rreq.destination_mac):
                            continue

                        # Only include short routes
                        if len(route.hops) <= 2:
                            # Get all the node info needed for the topology
                            for hop in route.hops:
                                entry_exists = False
                                for existing in e_rrep.packet_topology:
                                    if existing.bt_mac_address == hop.bt_mac_address:
                                        entry_exists = True
                                        break

                                if not entry_exists:
                                    e_rrep.packet_topology.append(hop)

                            # Add the destination node info as well
                            dest_exists = False
                            for existing in e_rrep.packet_topology:
                                if existing.bt_mac_address == route.host.bt_mac_address:
                                    dest_exists = True
                                    break

                            if not dest_exists:
                                e_rrep.packet_topology.append(route.host)

                            additional_routes += 1
                            if additional_routes >= 3:
                                break

            # Update source fields
            e_rrep.source_id = self.node_id
            e_rrep.source_mac = self.mac_address

            # # Prepare the reply with our information (this method doesn't exist)
            e_rrep.prepare_reply(
                node_mac=self.mac_address,
                node_id=self.node_id,
                neighbors=neighbor_macs
            )

            # Convert to JSON for sending - FIX SERIALIZATION HERE
            rrep_data = {
                "type": RequestType.E_RREP.value,
                "source_id": e_rrep.source_id,
                "source_mac": e_rrep.source_mac,
                "destination_id": e_rrep.destination_id,
                "destination_mac": e_rrep.destination_mac,
                "broadcast_id": e_rrep.broadcast_id,
                "sequence_number": e_rrep.sequence_number,
                "hop_count": e_rrep.hop_count,
                "timestamp": e_rrep.timestamp,
                "operation_type": e_rrep.operation_type,
                "response_data": e_rrep.response_data,
                "packet_topology": []  # Initialize as empty list
            }

            # FIX: Proper serialization of packet topology entries
            if e_rrep.packet_topology:
                topology_entries = []
                for entry in e_rrep.packet_topology:
                    # Safely convert each entry to dict
                    if hasattr(entry, 'asdict'):
                        topology_entries.append(asdict(entry))
                    elif hasattr(entry, '__dict__'):
                        # For non-dataclass objects with __dict__
                        topology_entries.append(entry.__dict__)
                    else:
                        # Entry is already a dict or primitive type
                        topology_entries.append({
                            "node_id": entry.node_id if hasattr(entry, "node_id") else None,
                            "bt_mac_address": entry.bt_mac_address if hasattr(entry, "bt_mac_address") else "",
                            "neighbors": entry.neighbors if hasattr(entry, "neighbors") else []
                        })
                rrep_data["packet_topology"] = topology_entries

            # Send to the previous hop
            self.bt_comm.send_json(sender_address, rrep_data)

            # Log with operation type
            op_type = OperationType(e_rreq.operation_type).name if e_rreq.operation_type in [e.value for e in
                                                                                             OperationType] else "UNKNOWN"

            logger.info(f"Sent E-RREP ({op_type}) to {sender_address} with optimized topology " +
                        f"({len(e_rrep.packet_topology)} entries, {additional_routes} additional routes)")

        except Exception as e:
            logger.error(f"Error generating route reply: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_route_reply(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Handle an incoming route reply (E-RREP).
        """
        try:
            # Extract basic information from the reply
            dest_mac = message_data.get("destination_mac", "")
            source_mac = message_data.get("source_mac", "")
            operation_type = message_data.get("operation_type", OperationType.ROUTE.value)

            # Always update our topology information from incoming replies
            if "packet_topology" in message_data and message_data["packet_topology"]:
                self._process_route_information(message_data)
                logger.info(f"Updated route information from E-RREP")

            # Check if we are the destination of this reply
            if dest_mac == self.mac_address:
                op_type = OperationType(operation_type).name if operation_type in [e.value for e in
                                                                                   OperationType] else "UNKNOWN"
                logger.info(f"Received E-RREP ({op_type}) destined for us from {source_mac}")

                if operation_type == OperationType.QUERY.value:
                    response_data = message_data.get("response_data", {}) or {}
                    # Check if this is a capabilities query response
                    if "capabilities" in response_data:
                        logger.info(f"Received capability data from {source_mac}, storing it")
                        self.node_capabilities[source_mac] = response_data["capabilities"]

                # Get both broadcast IDs
                broadcast_id = message_data.get("broadcast_id", "")
                original_request_id = message_data.get("original_request_id", "")

                # Handle query responses
                if operation_type == OperationType.QUERY.value:
                    with self.query_lock:
                        # Try to find callback using original_request_id first
                        query_callback = None

                        if original_request_id:
                            query_callback = self.pending_queries.pop(original_request_id, None)
                            if query_callback:
                                logger.info(f"Found query callback using original_request_id: {original_request_id}")

                        # Fall back to broadcast_id if original_request_id didn't work
                        if query_callback is None:
                            query_callback = self.pending_queries.pop(broadcast_id, None)
                            if query_callback:
                                logger.info(f"Found query callback using broadcast_id: {broadcast_id}")

                        if query_callback:
                            try:
                                response_data = message_data.get("response_data", {}) or {}

                                # Extract the query type from the response
                                response_keys = set(response_data.keys()) - {"status", "message"}
                                if "status" in response_data and response_data["status"] == "ok" and response_keys:
                                    # Log the successful response with a focus on the data
                                    query_type = next(iter(response_keys), "unknown")
                                    if "sensor:" in query_type:
                                        sensor_name = query_type.split(":", 1)[1]
                                        logger.info(f"Received sensor data: {sensor_name}={response_data[query_type]}")
                                    else:
                                        logger.info(f"Received {query_type} response")

                                # Call the callback with the response data
                                logger.info(f"Calling query callback with response")
                                query_callback(True, response_data)
                            except Exception as e:
                                logger.error(f"Error calling query callback: {e}")
                                import traceback
                                logger.error(traceback.format_exc())
                        else:
                            logger.warning(
                                f"No callback found for query response with ID: {original_request_id or broadcast_id}")
            else:
                # Forward the reply towards the destination
                self._forward_route_reply(message_data, sender_address)

        except Exception as e:
            logger.error(f"Error handling route reply: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _process_route_information(self, reply_data: Dict[str, Any]):
        """
        Process route information from a route reply.

        Args:
            reply_data: Route reply data
        """
        try:
            # Extract source information
            source_mac = reply_data.get("source_mac", "")
            source_id = reply_data.get("source_id", "")

            if not source_mac:
                logger.error("Route reply missing source MAC")
                return

            # Extract topology information
            topology_data = reply_data.get("packet_topology", [])
            if not topology_data:
                logger.warning(f"No topology data in reply from {source_id}")
                return

            # Build hop list
            hops = []
            for entry in topology_data:
                if isinstance(entry, dict):
                    hop = TopologyEntry(
                        node_id=entry.get("node_id"),
                        bt_mac_address=entry.get("bt_mac_address", ""),
                        neighbors=entry.get("neighbors", [])
                    )
                    hops.append(hop)

            # Process and clean up the hop list
            if hops:
                # Remove duplicate nodes and loops
                cleaned_hops = []
                seen_macs = set()

                for hop in hops:
                    # Skip if this is our own MAC (would create a loop)
                    if hop.bt_mac_address == self.mac_address:
                        continue

                    # Skip if we've already seen this MAC (duplicate/loop)
                    if hop.bt_mac_address in seen_macs:
                        logger.warning(f"Removing duplicate node {hop.bt_mac_address} from route")
                        continue

                    # Add to cleaned list and track MAC
                    cleaned_hops.append(hop)
                    seen_macs.add(hop.bt_mac_address)

                # Update route only if we have valid hop information
                if cleaned_hops:
                    with self.state_lock:
                        logger.info(f"Adding route to {source_id} ({source_mac}) with {len(cleaned_hops)} hops")
                        self.aodv_state.add_or_update_route(
                            dest_mac=source_mac,
                            dest_id=source_id,
                            hops=cleaned_hops
                        )

                        # Log the routing table state
                        routes = []
                        for route in self.aodv_state.routing_table:
                            dest = route.host.bt_mac_address
                            hop_count = len(route.hops)
                            routes.append(f"{dest} ({hop_count} hops)")

                        logger.info(
                            f"Current routing table entries: {len(routes)} - {', '.join(routes) if routes else 'none'}")
                else:
                    logger.warning(f"No valid hop information found in route reply from {source_id}")

        except Exception as e:
            logger.error(f"Error processing route information: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _extract_route_from_reply(self, reply_data: Dict[str, Any]) -> List[str]:
        """
        Extract route from a route reply.

        Args:
            reply_data: Route reply data

        Returns:
            List of node MAC addresses in the route
        """
        route = []

        # Check if packet_topology exists and has data
        if "packet_topology" not in reply_data or not reply_data["packet_topology"]:
            logger.warning("No packet topology found in reply data")
            return route

        topology_data = reply_data.get("packet_topology", [])

        for entry in topology_data:
            if isinstance(entry, dict):
                bt_mac = entry.get("bt_mac_address")
                if bt_mac:
                    route.append(bt_mac)

        logger.info(f"Extracted route: {route}")
        return route

    def _forward_route_reply(self, reply_data: Dict[str, Any], sender_address: str):
        """
        Forward a route reply towards its destination.

        Args:
            reply_data: Route reply data
            sender_address: Address of the node that sent us the reply
        """
        try:
            dest_mac = reply_data.get("destination_mac", "")
            operation_type = reply_data.get("operation_type", OperationType.ROUTE.value)

            if not dest_mac:
                logger.error("Route reply missing destination MAC")
                return

            # Get neighbors
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

            # Update hop count and append our information to topology
            reply_data["hop_count"] = reply_data.get("hop_count", 0) + 1
            reply_data["timestamp"] = str(time.time())

            # Add our topology entry
            topology_entry = {
                "node_id": self.node_id,
                "bt_mac_address": self.mac_address,
                "neighbors": neighbor_macs
            }

            if "packet_topology" not in reply_data:
                reply_data["packet_topology"] = []

            # Ensure existing topology is properly serialized
            if "packet_topology" in reply_data:
                # Create a new serializable list
                new_topology = []

                # Process existing entries first
                for entry in reply_data["packet_topology"]:
                    if isinstance(entry, dict):
                        new_topology.append(entry)  # Already in dict form
                    elif hasattr(entry, 'asdict'):
                        new_topology.append(asdict(entry))
                    elif hasattr(entry, '__dict__'):
                        new_topology.append(entry.__dict__)
                    else:
                        # Convert attributes to dict
                        new_topology.append({
                            "node_id": entry.node_id if hasattr(entry, "node_id") else None,
                            "bt_mac_address": entry.bt_mac_address if hasattr(entry, "bt_mac_address") else "",
                            "neighbors": entry.neighbors if hasattr(entry, "neighbors") else []
                        })

                # Replace with properly serialized list
                reply_data["packet_topology"] = new_topology

            # Now append our entry
            reply_data["packet_topology"].append(topology_entry)

            # Find next hop towards destination
            next_hop = self._find_next_hop_to_destination(dest_mac)

            if "original_request_id" in reply_data:
                reply_data["original_request_id"] = reply_data["original_request_id"]

            if next_hop:
                # Send to next hop
                self.bt_comm.send_json(next_hop, reply_data)

                # Log with operation type
                op_type = OperationType(operation_type).name if operation_type in [e.value for e in
                                                                                   OperationType] else "UNKNOWN"
                logger.info(f"Forwarded E-RREP ({op_type}) to {next_hop}")
            else:
                logger.warning(f"No route to forward E-RREP to {dest_mac}")

        except Exception as e:
            logger.error(f"Error forwarding route reply: {e}")

    def _find_next_hop_to_destination(self, dest_mac: str) -> Optional[str]:
        """
        Find the next hop towards a destination.

        Args:
            dest_mac: Destination MAC address

        Returns:
            Next hop MAC address or None if no route
        """
        with self.state_lock:
            # First check if we're directly connected
            for device_addr in self.bt_comm.connected_devices:
                if device_addr == dest_mac:
                    return dest_mac

            # Check routing table
            for route in self.aodv_state.routing_table:
                if route.host.bt_mac_address == dest_mac and route.hops:
                    # First hop in the route
                    return route.hops[0].bt_mac_address

        return None

    def _handle_route_error(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Handle an incoming route error (E-RERR) with network resilience.

        When a node disconnects, we only remove routes if we don't have
        an alternative direct connection to the supposedly failed node.

        Args:
            sender_address: MAC address of the sender
            message_data: Decoded E-RERR message
        """
        try:
            # Extract information
            failed_node_mac = message_data.get("failed_node_mac", "")
            failed_node_id = message_data.get("failed_node_id", "")
            originator_mac = message_data.get("originator_mac", "")
            reason = message_data.get("reason", "unknown")

            if not failed_node_mac:
                logger.error("Route error missing failed node MAC")
                return

            # Log receipt of the error
            logger.info(f"Received E-RERR: Node {failed_node_id or failed_node_mac} " +
                        f"reported failed by {originator_mac}, reason: {reason}")

            # Check if we have a direct connection to the supposedly failed node
            direct_connection_exists = False
            with self.state_lock:
                direct_connection_exists = failed_node_mac in self.bt_comm.connected_devices

            if direct_connection_exists:
                # We still have a direct connection to this node, so it's not truly "failed" for us
                logger.info(f"Node {failed_node_id or failed_node_mac} reported as failed, " +
                            f"but we still have a direct connection. Updating routes only.")

                # Just update routes that go through the originator to this node
                self._update_routes_for_partial_failure(originator_mac, failed_node_mac)
            else:
                # We don't have a direct connection, so treat this as a full node failure
                logger.info(f"No direct connection to {failed_node_id or failed_node_mac}. " +
                            f"Removing all routes through this node.")

                # Remove routes using the failed node
                self._remove_routes_through_node(failed_node_mac)

                # Forward the error to other nodes
                self._forward_route_error(message_data, sender_address)

        except Exception as e:
            logger.error(f"Error handling route error: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _update_routes_for_partial_failure(self, failed_link_mac: str, target_node_mac: str):
        """
        Update routes when a link between two nodes has failed, but the target
        node is still directly accessible to us.

        Args:
            failed_link_mac: MAC of the node that can't reach the target
            target_node_mac: MAC of the target node that is still directly connected to us
        """
        try:
            with self.state_lock:
                # Find routes to target_node_mac that go through failed_link_mac
                for route in self.aodv_state.routing_table:
                    if route.host.bt_mac_address == target_node_mac:
                        # Check if this route uses failed_link_mac as an intermediate hop
                        uses_failed_link = any(hop.bt_mac_address == failed_link_mac for hop in route.hops)

                        if uses_failed_link:
                            # Replace with direct route if we have a direct connection
                            if target_node_mac in self.bt_comm.connected_devices:
                                # Create a direct route (empty hops list)
                                logger.info(f"Updating route to {target_node_mac}: " +
                                            f"Replacing route through {failed_link_mac} with direct connection")
                                self.aodv_state.add_or_update_route(
                                    dest_mac=target_node_mac,
                                    dest_id=route.host.node_id,
                                    hops=[]  # Empty hops means direct connection
                                )

                # Also update any routes that use target_node_mac as an intermediate hop
                # to avoid using paths through the broken link
                routes_to_update = []
                for route in self.aodv_state.routing_table:
                    # Skip the target node itself
                    if route.host.bt_mac_address == target_node_mac:
                        continue

                    # Check if this route uses target_node_mac as a hop
                    for i, hop in enumerate(route.hops):
                        if hop.bt_mac_address == target_node_mac:
                            # Check if the route uses failed_link_mac -> target_node_mac path
                            if i > 0 and route.hops[i - 1].bt_mac_address == failed_link_mac:
                                routes_to_update.append(route.host.bt_mac_address)
                                break

                # Update these routes if needed - this might trigger new route discoveries
                for dest_mac in routes_to_update:
                    logger.info(f"Marking route to {dest_mac} for update due to link failure")
                    # We'll actually remove the route and let route discovery rebuild it
                    self.aodv_state.remove_route(dest_mac)

        except Exception as e:
            logger.error(f"Error updating routes for partial failure: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _remove_routes_through_node(self, node_mac: str):
        """
        Remove routes that go through a failed node.

        Args:
            node_mac: MAC address of the failed node
        """
        try:
            with self.state_lock:
                # First, remove direct route to the node
                self.aodv_state.remove_route(node_mac)

                # Then remove routes that use this node as a hop
                updated_routes = []

                for route in self.aodv_state.routing_table:
                    # Check if any hop uses the failed node
                    uses_failed_node = any(
                        hop.bt_mac_address == node_mac for hop in route.hops
                    )

                    if not uses_failed_node:
                        updated_routes.append(route)

                if len(updated_routes) < len(self.aodv_state.routing_table):
                    removed = len(self.aodv_state.routing_table) - len(updated_routes)
                    logger.info(f"Removed {removed} routes through failed node {node_mac}")

                self.aodv_state.routing_table = updated_routes

        except Exception as e:
            logger.error(f"Error removing routes through node: {e}")

    def _forward_route_error(self, error_data: Dict[str, Any], sender_address: str):
        """
        Forward a route error to other nodes.

        Args:
            error_data: Route error data
            sender_address: Address of the node that sent us the error
        """
        try:
            # Get connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            # Forward to all except the sender
            for device_addr in connected_devices:
                if device_addr != sender_address:
                    try:
                        self.bt_comm.send_json(device_addr, error_data)
                    except Exception as e:
                        logger.error(f"Failed to forward E-RERR to {device_addr}: {e}")

        except Exception as e:
            logger.error(f"Error forwarding route error: {e}")

    def _handle_hello(self, sender_address: str, message_data: Dict[str, Any]):
        """
        Handle an incoming hello message (R_HELLO) with enhanced topology information.

        Args:
            sender_address: MAC address of the sender
            message_data: Decoded R_HELLO message
        """
        try:
            # Extract information
            node_id = message_data.get("node_id", "")
            bt_mac_address = message_data.get("bt_mac_address", "")
            initial_neighbors = message_data.get("initial_neighbors", [])
            routing_knowledge = message_data.get("routing_knowledge", [])
            capabilities = message_data.get("capabilities", {})
            sensor_data = message_data.get("sensor_data", {})
            hello_interval = message_data.get("hello_interval", 30)  # Default to 30s
            include_sensor_data = message_data.get("include_sensor_data", False)

            if not bt_mac_address:
                logger.error("Hello message missing BT MAC address")
                return

            # Check if this is a new node we haven't seen before
            is_new_node = False
            with self.state_lock:
                is_new_node = not any(n.bt_mac_address == bt_mac_address for n in self.aodv_state.neighbours)

            # Update neighbor information
            with self.state_lock:
                # Add/update neighbor
                self.aodv_state.add_neighbor(
                    neighbor_mac=bt_mac_address,
                    neighbor_id=node_id,
                    neighbors=initial_neighbors
                )

                # Store neighbor capabilities
                self.node_capabilities[bt_mac_address] = capabilities
                self.neighbor_capabilities[bt_mac_address] = capabilities  # Keep for backward compatibility

                # Process routing knowledge from hello message
                if routing_knowledge:
                    logger.info(f"Received {len(routing_knowledge)} routes in hello from {node_id}")
                    routes_added = 0

                    # Incorporate routing knowledge into our routing table
                    for route_info in routing_knowledge:
                        dest = route_info.get("destination", {})
                        dest_mac = dest.get("bt_mac_address")
                        dest_capabilities = dest.get("capabilities")
                        if dest_capabilities and dest_mac:
                            self.node_capabilities[dest_mac] = dest_capabilities
                            logger.debug(f"Updated capabilities for remote node {dest_mac} from routing knowledge")

                        # Skip invalid routes, routes to self, or routes we already have direct connections to
                        if (not dest_mac or
                                dest_mac == self.mac_address or
                                dest_mac in self.bt_comm.connected_devices):
                            continue

                        # Build a new route from scratch - MAJOR FIX HERE
                        new_route = []

                        # CRITICAL: First hop must be the sender of the hello message
                        # This is our direct connection to the route
                        sender_hop = TopologyEntry(
                            node_id=node_id,
                            bt_mac_address=bt_mac_address,
                            neighbors=initial_neighbors
                        )
                        new_route.append(sender_hop)

                        # Then add any additional hops from the route
                        # but avoid loops, duplicates, and ensure they're in the correct order
                        has_loop = False

                        # Get the hops in the correct order (the order matters!)
                        hop_list = route_info.get("hops", [])

                        # Process each hop
                        for hop_info in hop_list:
                            if isinstance(hop_info, dict):
                                hop_mac = hop_info.get("bt_mac_address")
                                hop_node_id = hop_info.get("node_id")

                                # Skip adding this hop if it's the same as any existing hop
                                if any(h.bt_mac_address == hop_mac for h in new_route):
                                    continue

                                # Skip if it's the sender (which we already added as first hop)
                                if hop_mac == bt_mac_address:
                                    continue

                                # Check for routing loops involving our address
                                if hop_mac == self.mac_address:
                                    has_loop = True
                                    break

                                # Get capabilities if available
                                hop_capabilities = hop_info.get("capabilities")
                                if hop_capabilities and hop_mac:
                                    self.node_capabilities[hop_mac] = hop_capabilities

                                # Add this hop to our route
                                hop = TopologyEntry(
                                    node_id=hop_node_id,
                                    bt_mac_address=hop_mac,
                                    neighbors=hop_info.get("neighbors", [])
                                )
                                new_route.append(hop)

                        # Only add route if no loops were detected and we have valid hops
                        if not has_loop and new_route:
                            # Check if we already have a better route
                            existing_route = next(
                                (r for r in self.aodv_state.routing_table if r.host.bt_mac_address == dest_mac),
                                None
                            )

                            # Add or update route if:
                            # 1. We don't have an existing route, or
                            # 2. This route is shorter than our existing route
                            if not existing_route or len(new_route) < len(existing_route.hops):
                                self.aodv_state.add_or_update_route(
                                    dest_mac=dest_mac,
                                    dest_id=dest.get("node_id"),
                                    hops=new_route
                                )
                                routes_added += 1

                                # Log the constructed route for debugging
                                hop_description = " -> ".join([f"{h.node_id or h.bt_mac_address}" for h in new_route])
                                logger.info(f"Added route to {dest.get('node_id') or dest_mac} via: {hop_description}")

                    if routes_added > 0:
                        logger.info(f"Added/updated {routes_added} routes from hello message")

            # Log capabilities and sensor data
            temp_log = ""
            if "temperature_value" in capabilities:
                temp_log = f", Temperature: {capabilities['temperature_value']}°C"

            capability_str = ", ".join([
                f"{cap}" for cap in capabilities.keys()
                if cap != "temperature_value" and not cap.endswith("_writable") and capabilities[cap] is True
            ])

            # Log writable capabilities
            writable_caps = []
            for cap in capabilities.keys():
                if cap.endswith("_writable") and capabilities[cap] is True:
                    sensor_name = cap.replace("_writable", "")
                    writable_caps.append(sensor_name)

            if writable_caps:
                writable_str = ", ".join(writable_caps)
                if capability_str:
                    capability_str += f", Writable: {writable_str}"
                else:
                    capability_str = f"Writable: {writable_str}"

            if capability_str:
                logger.info(f"Updated neighbor: {node_id} ({bt_mac_address}){temp_log}, Capabilities: {capability_str}")
            else:
                logger.info(f"Updated neighbor: {node_id} ({bt_mac_address}){temp_log}")

            # Log sensor data if present
            if sensor_data:
                sensor_str = ", ".join([f"{k}: {v}" for k, v in sensor_data.items()])
                logger.info(f"Received sensor data from {node_id}: {sensor_str}")

            # If this is a new node, send our network configuration to it
            # if is_new_node:
            #     logger.info(f"New node detected: {node_id} ({bt_mac_address}). Syncing network configuration...")
            #     self._sync_config_to_new_node(bt_mac_address)

        except Exception as e:
            logger.error(f"Error handling hello message: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_query_request(self, e_rreq: E_RREQ, sender_address: str):
        """
        Handle a query request.

        Args:
            e_rreq: Enhanced Route Request with query parameters
            sender_address: Address of the node that sent us the request
        """
        try:
            # Create a route reply from the request
            e_rrep = E_RREP.from_erreq(e_rreq)
            e_rrep.operation_type = OperationType.QUERY.value

            # Get neighbors
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

            # Update source fields
            e_rrep.source_id = self.node_id
            e_rrep.source_mac = self.mac_address

            # Prepare the reply with our information
            e_rrep.prepare_reply(
                node_mac=self.mac_address,
                node_id=self.node_id,
                neighbors=neighbor_macs
            )

            # Process the query
            query_params = e_rreq.query_params if e_rreq.query_params else {}
            query_type = query_params.get("query", "")
            response_data = {"status": "ok"}  # Initialize with a default status

            logger.info(f"Processing query of type: {query_type} from {e_rreq.source_id} ({e_rreq.source_mac})")

            # Handle different query types
            if query_type.startswith("sensor:"):
                # Extract sensor type from query
                sensor_type = query_type.split(":", 1)[1]
                sensor = self.sensor_registry.get_sensor(sensor_type)

                if sensor and sensor.enabled:
                    try:
                        value = sensor.read()
                        response_data[sensor_type] = value
                        response_data["status"] = "ok"
                        logger.info(f"Read {sensor_type} sensor: {value}")
                    except Exception as e:
                        response_data["status"] = "error"
                        response_data["message"] = f"Error reading {sensor_type} sensor: {str(e)}"
                        logger.error(f"Error reading {sensor_type} sensor: {e}")
                else:
                    response_data["status"] = "error"
                    response_data["message"] = f"Sensor {sensor_type} not available or disabled"
                    logger.warning(f"Requested sensor {sensor_type} not available or disabled")

            # Legacy handling for backward compatibility
            elif query_type in self.sensor_registry.get_all_capabilities():
                # Direct sensor query without the "sensor:" prefix
                sensor = self.sensor_registry.get_sensor(query_type)
                if sensor and sensor.enabled:
                    try:
                        value = sensor.read()
                        response_data[query_type] = value
                        response_data["status"] = "ok"
                        logger.info(f"Read {query_type} sensor: {value}")
                    except Exception as e:
                        response_data["status"] = "error"
                        response_data["message"] = f"Error reading {query_type} sensor: {str(e)}"
                        logger.error(f"Error reading {query_type} sensor: {e}")
                else:
                    response_data["status"] = "error"
                    response_data["message"] = f"{query_type} sensor not available or disabled"
                    logger.warning(f"Requested sensor {query_type} not available or disabled")

            # Other non-sensor query types
            elif query_type == "capabilities":
                response_data["capabilities"] = self.sensor_registry.get_all_capabilities()
                response_data["status"] = "ok"
                logger.info(f"Processing capabilities query")

            elif query_type == "network_config":
                response_data["hello_interval"] = self.network_config.hello_interval
                response_data["include_sensor_data"] = self.network_config.include_sensor_data
                response_data["ttl_default"] = self.network_config.ttl_default
                response_data["route_cache_timeout"] = self.network_config.route_cache_timeout
                response_data["status"] = "ok"
                logger.info(f"Processing network_config query")

            elif query_type == "neighbors":
                neighbor_list = []
                with self.state_lock:
                    for neighbor in self.aodv_state.neighbours:
                        neighbor_list.append({
                            "node_id": neighbor.node_id,
                            "bt_mac_address": neighbor.bt_mac_address
                        })
                response_data["neighbors"] = neighbor_list
                response_data["status"] = "ok"
                logger.info(f"Processing neighbors query, found {len(neighbor_list)} neighbors")

            else:
                response_data["status"] = "error"
                response_data["message"] = f"Unsupported query type: {query_type}"
                logger.warning(f"Unsupported query type received: {query_type}")

            # Set response data
            e_rrep.response_data = response_data

            # Update routing table based on the RREQ packet topology
            # Update routing table based on the RREQ packet topology
            if e_rreq.packet_topology:
                # Create a copy of the topology and reverse it
                hops = []
                for entry in e_rreq.packet_topology:
                    if hasattr(entry, 'bt_mac_address'):
                        hops.append(entry)

                # IMPORTANT FIX: Reverse the hops to create route from current node to source
                hops.reverse()

                # Remove duplicate nodes and loops
                cleaned_hops = []
                seen_macs = set()

                for hop in hops:
                    # Skip if this is our own MAC (would create a loop)
                    if hop.bt_mac_address == self.mac_address:
                        continue

                    # Skip if we've already seen this MAC (duplicate/loop)
                    if hop.bt_mac_address in seen_macs:
                        logger.warning(f"Removing duplicate node {hop.bt_mac_address} from route")
                        continue

                    # Add to cleaned list and track MAC
                    cleaned_hops.append(hop)
                    seen_macs.add(hop.bt_mac_address)

                # Ensure the first hop is correct for this route
                if cleaned_hops and cleaned_hops[0].bt_mac_address != sender_address:
                    # First check if the sender is already in our hop list
                    sender_in_hops = False
                    for i, hop in enumerate(cleaned_hops):
                        if hop.bt_mac_address == sender_address:
                            # Move sender to front
                            cleaned_hops.insert(0, cleaned_hops.pop(i))
                            sender_in_hops = True
                            break

                    if not sender_in_hops:
                        # Add sender as first hop
                        from e_aodv_models import TopologyEntry
                        sender_entry = TopologyEntry(
                            node_id=None,  # We might not know the ID
                            bt_mac_address=sender_address,
                            neighbors=[]
                        )
                        cleaned_hops.insert(0, sender_entry)

                # Add route to the source
                if cleaned_hops:
                    with self.state_lock:
                        logger.info(
                            f"Updating route to {e_rreq.source_id} ({e_rreq.source_mac}) with {len(cleaned_hops)} hops")
                        self.aodv_state.add_or_update_route(
                            dest_mac=e_rreq.source_mac,
                            dest_id=e_rreq.source_id,
                            hops=cleaned_hops
                        )

            # Convert to JSON for sending
            rrep_data = {
                "type": RequestType.E_RREP.value,
                "source_id": e_rrep.source_id,
                "source_mac": e_rrep.source_mac,
                "destination_id": e_rrep.destination_id,
                "destination_mac": e_rrep.destination_mac,
                "broadcast_id": e_rrep.broadcast_id,
                "original_request_id": e_rrep.original_request_id,
                "sequence_number": e_rrep.sequence_number,
                "hop_count": e_rrep.hop_count,
                "timestamp": e_rrep.timestamp,
                "operation_type": e_rrep.operation_type,
                "response_data": e_rrep.response_data,
            }

            # Add the packet topology data
            rrep_data["packet_topology"] = []
            if e_rrep.packet_topology:
                for entry in e_rrep.packet_topology:
                    entry_dict = {
                        "node_id": entry.node_id if hasattr(entry, "node_id") else None,
                        "bt_mac_address": entry.bt_mac_address if hasattr(entry, "bt_mac_address") else "",
                        "neighbors": entry.neighbors if hasattr(entry, "neighbors") else []
                    }
                    rrep_data["packet_topology"].append(entry_dict)

            # Find next hop to the source
            next_hop = self._find_next_hop_to_destination(e_rrep.destination_mac)

            # If no route found, use the previous hop as direct fallback
            if not next_hop:
                next_hop = sender_address
                logger.info(f"No route to {e_rrep.destination_mac}, sending directly to previous hop {sender_address}")

            # Send the reply
            success = self.bt_comm.send_json(next_hop, rrep_data)
            if success:
                logger.info(f"Successfully sent E-RREP response to {e_rrep.destination_id} via {next_hop}")
            else:
                logger.error(f"Failed to send E-RREP to {next_hop}")

        except Exception as e:
            logger.error(f"Error handling query request: {e}")
            import traceback
            logger.error(traceback.format_exc())


    def _handle_write_request(self, e_rreq: E_RREQ, sender_address: str):
        """
        Handle a write request.

        Args:
            e_rreq: Enhanced Route Request with write parameters
            sender_address: Address of the node that sent us the request
        """
        try:
            # Create a route reply from the request
            e_rrep = E_RREP.from_erreq(e_rreq)
            e_rrep.operation_type = OperationType.WRITE.value

            # Get neighbors and prepare the reply
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

            # Update source fields
            e_rrep.source_id = self.node_id
            e_rrep.source_mac = self.mac_address

            # Prepare the reply with our information
            e_rrep.prepare_reply(
                node_mac=self.mac_address,
                node_id=self.node_id,
                neighbors=neighbor_macs
            )

            # Process the write request
            write_params = e_rreq.query_params
            response_data = {"status": "ok", "updated_keys": []}

            # Log the incoming request
            logger.info(f"Received write request: {write_params}")

            # Process each parameter
            for param_name, param_value in write_params.items():
                # Check if this parameter corresponds directly to a sensor
                sensor = self.sensor_registry.get_sensor(param_name)
                if sensor and sensor.writable and sensor.enabled:
                    # Direct sensor write using our sensor registry
                    if self.sensor_registry.write_sensor(param_name, param_value):
                        response_data["updated_keys"].append(param_name)
                        logger.info(f"Wrote {param_value} to sensor {param_name}")
                    else:
                        logger.error(f"Failed to write {param_value} to sensor {param_name}")
                    continue

                # Handle legacy named parameters (backwards compatibility)
                if param_name == "motor":
                    sensor = self.sensor_registry.get_sensor("motor")
                    if sensor and sensor.writable and sensor.enabled:
                        if self.sensor_registry.write_sensor("motor", param_value):
                            response_data["updated_keys"].append("motor")
                            logger.info(f"Set motor to {param_value}")
                        else:
                            logger.error(f"Failed to set motor to {param_value}")
                    else:
                        logger.warning("Motor write requested but no writable motor sensor available")

                elif param_name == "led":
                    sensor = self.sensor_registry.get_sensor("led")
                    if sensor and sensor.writable and sensor.enabled:
                        if self.sensor_registry.write_sensor("led", param_value):
                            response_data["updated_keys"].append("led")
                            logger.info(f"Set LED to {param_value}")
                        else:
                            logger.error(f"Failed to set LED to {param_value}")
                    else:
                        logger.warning("LED write requested but no writable LED sensor available")

                elif param_name == "display":
                    sensor = self.sensor_registry.get_sensor("display")
                    if sensor and sensor.writable and sensor.enabled:
                        if self.sensor_registry.write_sensor("display", param_value):
                            response_data["updated_keys"].append("display")
                            logger.info(f"Set display message to: {param_value}")
                        else:
                            logger.error(f"Failed to set display message to: {param_value}")
                    else:
                        # Legacy support if display is in capabilities but not a sensor
                        if "display" in self.capabilities and self.capabilities["display"]:
                            logger.info(f"Legacy display write: {param_value}")
                            response_data["updated_keys"].append("display")
                        else:
                            logger.warning("Display write requested but not available")

            # Update response status based on success
            if not response_data["updated_keys"]:
                response_data["status"] = "error"
                response_data["message"] = "No valid write parameters found"

            if "original_request_id" in response_data:
                response_data["original_request_id"] = response_data["original_request_id"]

            # Set response data
            e_rrep.response_data = response_data

            # Convert to JSON for sending
            rrep_data = {
                "type": RequestType.E_RREP.value,
                "source_id": e_rrep.source_id,
                "source_mac": e_rrep.source_mac,
                "destination_id": e_rrep.destination_id,
                "destination_mac": e_rrep.destination_mac,
                "broadcast_id": e_rrep.broadcast_id,
                "sequence_number": e_rrep.sequence_number,
                "hop_count": e_rrep.hop_count,
                "timestamp": e_rrep.timestamp,
                "operation_type": e_rrep.operation_type,
                "response_data": e_rrep.response_data,
                "packet_topology": [asdict(entry) for entry in e_rrep.packet_topology]
                if hasattr(e_rrep.packet_topology[0], 'asdict') else e_rrep.packet_topology
            }

            # Send to the previous hop
            self.bt_comm.send_json(sender_address, rrep_data)
            logger.info(f"Sent write response to {sender_address}")

        except Exception as e:
            logger.error(f"Error handling write request: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_config_request(self, e_rreq: E_RREQ, sender_address: str):
        """
        Handle a network configuration request.

        Args:
            e_rreq: Enhanced Route Request with configuration parameters
            sender_address: Address of the node that sent us the request
        """
        try:
            # Create a route reply from the request
            e_rrep = E_RREP.from_erreq(e_rreq)
            e_rrep.operation_type = OperationType.CONFIG.value

            # Get neighbors
            with self.state_lock:
                neighbor_macs = [n.bt_mac_address for n in self.aodv_state.neighbours]

            # Update source fields
            e_rrep.source_id = self.node_id
            e_rrep.source_mac = self.mac_address

            # Prepare the reply with our information
            e_rrep.prepare_reply(
                node_mac=self.mac_address,
                node_id=self.node_id,
                neighbors=neighbor_macs
            )

            # Process the configuration request
            config_params = e_rreq.query_params
            response_data = {"status": "ok", "updated_params": []}

            # Update network configuration parameters
            if "hello_interval" in config_params:
                hello_interval = config_params["hello_interval"]
                if isinstance(hello_interval, (int, float)) and hello_interval > 0:
                    self.network_config.hello_interval = hello_interval
                    response_data["updated_params"].append("hello_interval")
                    logger.info(f"Updated hello_interval to {hello_interval}")

            if "include_sensor_data" in config_params:
                include_sensor_data = config_params["include_sensor_data"]
                if isinstance(include_sensor_data, bool):
                    self.network_config.include_sensor_data = include_sensor_data
                    response_data["updated_params"].append("include_sensor_data")
                    logger.info(f"Updated include_sensor_data to {include_sensor_data}")

            if "ttl_default" in config_params:
                ttl_default = config_params["ttl_default"]
                if isinstance(ttl_default, int) and ttl_default > 0:
                    self.network_config.ttl_default = ttl_default
                    response_data["updated_params"].append("ttl_default")
                    logger.info(f"Updated ttl_default to {ttl_default}")

            if "route_cache_timeout" in config_params:
                route_cache_timeout = config_params["route_cache_timeout"]
                if isinstance(route_cache_timeout, int) and route_cache_timeout > 0:
                    self.network_config.route_cache_timeout = route_cache_timeout
                    response_data["updated_params"].append("route_cache_timeout")
                    logger.info(f"Updated route_cache_timeout to {route_cache_timeout}")

            # Set response data
            e_rrep.response_data = response_data

            # Convert to JSON for sending
            rrep_data = {
                "type": RequestType.E_RREP.value,
                "source_id": e_rrep.source_id,
                "source_mac": e_rrep.source_mac,
                "destination_id": e_rrep.destination_id,
                "destination_mac": e_rrep.destination_mac,
                "broadcast_id": e_rrep.broadcast_id,
                "original_request_id": e_rrep.original_request_id,
                "sequence_number": e_rrep.sequence_number,
                "hop_count": e_rrep.hop_count,
                "timestamp": e_rrep.timestamp,
                "operation_type": e_rrep.operation_type,
                "response_data": e_rrep.response_data,
                "packet_topology": []
            }

            # Add properly serialized topology data if available
            if e_rrep.packet_topology:
                topology_entries = []
                for entry in e_rrep.packet_topology:
                    if hasattr(entry, 'asdict'):
                        topology_entries.append(asdict(entry))
                    else:
                        topology_entries.append({
                            "node_id": entry.node_id if hasattr(entry, "node_id") else None,
                            "bt_mac_address": entry.bt_mac_address if hasattr(entry, "bt_mac_address") else "",
                            "neighbors": entry.neighbors if hasattr(entry, "neighbors") else []
                        })
                rrep_data["packet_topology"] = topology_entries

            # Send to the previous hop
            self.bt_comm.send_json(sender_address, rrep_data)
            logger.info(f"Sent configuration response to {sender_address}")

            # If any parameters were updated, also forward the config to all neighbors
            if response_data["updated_params"]:
                logger.info(f"Configuration changes applied, forwarding to network")

                # Use the regular broadcast deduplication mechanism
                # Forward to all nodes except the one who sent us the config
                self._propagate_config_to_neighbors(e_rreq.query_params, sender_address)

        except Exception as e:
            logger.error(f"Error handling config request: {e}")

    def _propagate_config_to_neighbors(self, config_params: Dict[str, Any], source_mac: str):
        """
        Propagate configuration changes to neighbors.

        Args:
            config_params: Configuration parameters to propagate
            source_mac: MAC address of the source node (to avoid sending back to it)
        """
        try:
            logger.info("Propagating network configuration to neighbors")

            # Get connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            if not connected_devices:
                logger.warning("No connected devices to propagate configuration to")
                return

            # Increment sequence number
            with self.state_lock:
                self.aodv_state.increment_sequence_number()
                sequence_number = self.aodv_state.sequence_number

            # Create a copy of the config params for propagation
            propagation_params = config_params.copy()

            # Use the default TTL from network configuration
            # This allows the propagation distance to be controlled via the normal TTL parameter
            propagation_ttl = self.network_config.ttl_default

            logger.info(f"Propagating configuration to {len(connected_devices)} devices with TTL={propagation_ttl}")

            # Track how many devices we sent to
            sent_count = 0

            # Send to all neighbors except the source
            for device_addr in connected_devices:
                # Skip the source to avoid loops
                if device_addr == source_mac:
                    logger.debug(f"Skipping propagation to source: {source_mac}")
                    continue

                try:
                    # Create a new request with a unique broadcast ID
                    broadcast_id = f"cfg-{uuid.uuid4().hex}"

                    # Convert to JSON directly for immediate sending
                    request_data = {
                        "type": RequestType.E_RREQ.value,
                        "source_id": self.node_id,
                        "source_mac": self.mac_address,
                        "destination_mac": device_addr,  # Empty for broadcast
                        "broadcast_id": broadcast_id,
                        "sequence_number": sequence_number,
                        "hop_count": 0,
                        "time_to_live": propagation_ttl,
                        "timestamp": str(time.time()),
                        "previous_hop_mac": self.mac_address,
                        "operation_type": OperationType.CONFIG.value,
                        "query_params": propagation_params,
                        "packet_topology": []
                    }

                    # Mark as processed to avoid loops if it comes back to us
                    with self.state_lock:
                        self.processed_route_requests.add(broadcast_id)

                    # Send to neighbor and log result
                    success = self.bt_comm.send_json(device_addr, request_data)
                    if success:
                        sent_count += 1
                        logger.info(f"Propagated configuration to {device_addr}")
                    else:
                        logger.error(f"Failed to send configuration to {device_addr}")

                except Exception as e:
                    logger.error(f"Failed to propagate config to {device_addr}: {e}")

            logger.info(f"Configuration propagated to {sent_count} devices")

        except Exception as e:
            logger.error(f"Error propagating configuration: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def discover_route(self, dest_id: str, dest_mac: Optional[str] = None,
                       callback: Optional[Callable[[bool, Optional[List[str]]], None]] = None):
        """
        Discover a route to a destination.

        Args:
            dest_id: Destination node ID
            dest_mac: Destination MAC address (if known)
            callback: Callback function to call when route is discovered or times out
                     The callback receives (success, route) where:
                     - success is a boolean indicating if route was found
                     - route is a list of MAC addresses or None if not found

        Returns:
            broadcast_id: ID of the broadcast request or None if route already exists
        """
        try:
            # Check if directly connected (direct route)
            if dest_mac and dest_mac in self.bt_comm.connected_devices:
                logger.info(f"Route found! Direct connection to {dest_id or dest_mac}")
                if callback:
                    # Empty route list means direct connection
                    callback(True, [dest_mac])
                return None

            # Check if we have a route in the routing table (indirect route)
            if dest_mac:
                with self.state_lock:
                    for route in self.aodv_state.routing_table:
                        if route.host.bt_mac_address == dest_mac:
                            route_macs = [hop.bt_mac_address for hop in route.hops]
                            via_node = route_macs[0] if route_macs else "unknown"
                            logger.info(f"Route found! Indirect via {via_node} to {dest_id or dest_mac}")
                            if callback:
                                callback(True, route_macs)
                            return None

            # No existing route, start discovery
            with self.state_lock:
                # Increment sequence number
                self.aodv_state.increment_sequence_number()
                sequence_number = self.aodv_state.sequence_number

            # Create route request
            e_rreq = E_RREQ(
                src_id=self.node_id,
                src_mac=self.mac_address,
                dest_id=dest_id,
                sequence_number=sequence_number,
                time_to_live=self.network_config.ttl_default,
                dest_mac=dest_mac
            )

            # Prepare for sending
            e_rreq.send_prepare()

            # Store in pending requests
            with self.state_lock:
                self.pending_route_requests[e_rreq.broadcast_id] = {
                    "timestamp": time.time(),
                    "dest_id": dest_id,
                    "dest_mac": dest_mac
                }

                # Store callback
                if callback and dest_mac:
                    self.route_discovery_callbacks[dest_mac] = callback

            # Convert to JSON
            request_data = {
                "type": RequestType.E_RREQ.value,
                "source_id": e_rreq.source_id,
                "source_mac": e_rreq.source_mac,
                "destination_id": e_rreq.destination_id,
                "destination_mac": e_rreq.destination_mac,
                "broadcast_id": e_rreq.broadcast_id,
                "sequence_number": e_rreq.sequence_number,
                "hop_count": e_rreq.hop_count,
                "time_to_live": e_rreq.time_to_live,
                "timestamp": e_rreq.timestamp,
                "previous_hop_mac": e_rreq.previous_hop_mac,
                "operation_type": e_rreq.operation_type,
                "query_params": e_rreq.query_params,
                "packet_topology": []
            }

            # Get connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            # Broadcast to all connected devices
            for device_addr in connected_devices:
                try:
                    self.bt_comm.send_json(device_addr, request_data)
                except Exception as e:
                    logger.error(f"Failed to send E-RREQ to {device_addr}: {e}")

            logger.info(f"Broadcasting E-RREQ for {dest_id or dest_mac} to {len(connected_devices)} devices")

            return e_rreq.broadcast_id

        except Exception as e:
            logger.error(f"Error discovering route: {e}")
            if callback:
                callback(False, None)
            return None

    def query_node(self, dest_mac: str, query_type: str,
                   callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query data from a remote node.

        Args:
            dest_mac: Destination MAC address
            query_type: Type of query (e.g., "sensor:temperature", "capabilities", "network_config")
            callback: Callback function to call when response is received
                    The callback receives (success, response_data)

        Returns:
            broadcast_id: ID of the broadcast request or None if route already exists
        """
        try:
            # Safety check - validate inputs
            if not dest_mac:
                logger.error("Cannot query node: No destination MAC provided")
                if callback:
                    callback(False, {"status": "error", "message": "No destination MAC provided"})
                return None

            if not query_type:
                logger.error("Cannot query node: No query type provided")
                if callback:
                    callback(False, {"status": "error", "message": "No query type provided"})
                return None

            # First check if we need to discover a route
            next_hop = self._find_next_hop_to_destination(dest_mac)

            # Increment sequence number
            with self.state_lock:
                self.aodv_state.increment_sequence_number()
                sequence_number = self.aodv_state.sequence_number

            # Create query request
            query_req = E_RREQ(
                src_id=self.node_id,
                src_mac=self.mac_address,
                dest_id="",  # Not needed for querying by MAC
                dest_mac=dest_mac,
                sequence_number=sequence_number,
                time_to_live=self.network_config.ttl_default,
                operation_type=OperationType.QUERY.value,
                query_params={"query": query_type}
            )

            # Prepare for sending
            query_req.send_prepare()

            # Store callback for when response is received
            if callback:
                with self.query_lock:
                    self.pending_queries[query_req.broadcast_id] = callback
                    logger.info(f"Stored query callback for broadcast ID: {query_req.broadcast_id}")

            # Log the query broadcast ID for easier debugging
            logger.info(
                f"Created query request (broadcast ID: {query_req.broadcast_id}) for '{query_type}' to {dest_mac}")

            # Convert to JSON
            request_data = {
                "type": RequestType.E_RREQ.value,
                "source_id": query_req.source_id,
                "source_mac": query_req.source_mac,
                "destination_id": query_req.destination_id,
                "destination_mac": query_req.destination_mac,
                "broadcast_id": query_req.broadcast_id,
                "sequence_number": query_req.sequence_number,
                "hop_count": query_req.hop_count,
                "time_to_live": query_req.time_to_live,
                "timestamp": query_req.timestamp,
                "previous_hop_mac": query_req.previous_hop_mac,
                "operation_type": query_req.operation_type,
                "query_params": query_req.query_params,
                "packet_topology": []
            }

            # Send the query and return the broadcast ID
            return self._send_query_request(request_data, dest_mac, next_hop, callback)

        except Exception as e:
            logger.error(f"Error querying node: {e}")
            import traceback
            logger.error(traceback.format_exc())
            if callback:
                callback(False, {"status": "error", "message": f"Error querying node: {e}"})
            return None

    def write_to_node(self, dest_mac: str, write_data: Dict[str, Any],
                      callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Write data to a remote node.

        Args:
            dest_mac: Destination MAC address
            write_data: Data to write to the node
            callback: Callback function to call when response is received
                     The callback receives (success, response_data)

        Returns:
            broadcast_id: ID of the broadcast request or None if route already exists
        """
        try:
            # First check if we need to discover a route
            next_hop = self._find_next_hop_to_destination(dest_mac)

            # Increment sequence number
            with self.state_lock:
                self.aodv_state.increment_sequence_number()
                sequence_number = self.aodv_state.sequence_number

            # Create write request
            write_req = E_RREQ(
                src_id=self.node_id,
                src_mac=self.mac_address,
                dest_id="",  # Not needed for writing by MAC
                dest_mac=dest_mac,
                sequence_number=sequence_number,
                time_to_live=self.network_config.ttl_default,
                operation_type=OperationType.WRITE.value,
                query_params=write_data
            )

            # Prepare for sending
            write_req.send_prepare()

            # Store callback for when response is received
            if callback:
                with self.query_lock:
                    self.pending_queries[write_req.broadcast_id] = callback

            # Convert to JSON
            request_data = {
                "type": RequestType.E_RREQ.value,
                "source_id": write_req.source_id,
                "source_mac": write_req.source_mac,
                "destination_id": write_req.destination_id,
                "destination_mac": write_req.destination_mac,
                "broadcast_id": write_req.broadcast_id,
                "sequence_number": write_req.sequence_number,
                "hop_count": write_req.hop_count,
                "time_to_live": write_req.time_to_live,
                "timestamp": write_req.timestamp,
                "previous_hop_mac": write_req.previous_hop_mac,
                "operation_type": write_req.operation_type,
                "query_params": write_req.query_params,
                "packet_topology": []
            }

            # If we have a next hop, send directly to it
            if next_hop:
                try:
                    self.bt_comm.send_json(next_hop, request_data)
                    logger.info(f"Sent write request to {dest_mac} via {next_hop}")
                except Exception as e:
                    logger.error(f"Failed to send write to {next_hop}: {e}")
                    if callback:
                        callback(False, {"status": "error", "message": f"Failed to send write: {e}"})
                    return None
            else:
                # Broadcast to all connected devices
                with self.state_lock:
                    connected_devices = list(self.bt_comm.connected_devices.keys())

                sent = False
                for device_addr in connected_devices:
                    try:
                        self.bt_comm.send_json(device_addr, request_data)
                        sent = True
                    except Exception as e:
                        logger.error(f"Failed to send write to {device_addr}: {e}")

                if sent:
                    logger.info(f"Broadcast write request to {len(connected_devices)} devices")
                else:
                    if callback:
                        callback(False, {"status": "error", "message": "No connected devices to send write"})
                    return None

            return write_req.broadcast_id

        except Exception as e:
            logger.error(f"Error writing to node: {e}")
            if callback:
                callback(False, {"status": "error", "message": f"Error writing to node: {e}"})
            return None

    def configure_network(self, config_params: Dict[str, Any],
                          callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Configure network parameters on all nodes.

        Args:
            config_params: Configuration parameters to set
            callback: Callback function to call when a response is received
                     The callback receives (success, response_data)

        Returns:
            True if configuration was sent, False otherwise
        """
        try:
            # First apply configuration to local node
            local_updates = []

            if "hello_interval" in config_params:
                hello_interval = config_params["hello_interval"]
                if isinstance(hello_interval, (int, float)) and hello_interval > 0:
                    self.network_config.hello_interval = hello_interval
                    local_updates.append("hello_interval")
                    logger.info(f"Updated local hello_interval to {hello_interval}")

            if "include_sensor_data" in config_params:
                include_sensor_data = config_params["include_sensor_data"]
                if isinstance(include_sensor_data, bool):
                    self.network_config.include_sensor_data = include_sensor_data
                    local_updates.append("include_sensor_data")
                    logger.info(f"Updated local include_sensor_data to {include_sensor_data}")

            if "ttl_default" in config_params:
                ttl_default = config_params["ttl_default"]
                if isinstance(ttl_default, int) and ttl_default > 0:
                    self.network_config.ttl_default = ttl_default
                    local_updates.append("ttl_default")
                    logger.info(f"Updated local ttl_default to {ttl_default}")

            if "route_cache_timeout" in config_params:
                route_cache_timeout = config_params["route_cache_timeout"]
                if isinstance(route_cache_timeout, int) and route_cache_timeout > 0:
                    self.network_config.route_cache_timeout = route_cache_timeout
                    local_updates.append("route_cache_timeout")
                    logger.info(f"Updated local route_cache_timeout to {route_cache_timeout}")

            # Then propagate to all neighbors
            self._propagate_config_to_neighbors(config_params, "")

            # Call callback with local update status
            if callback:
                callback(True, {
                    "status": "ok",
                    "local_updates": local_updates,
                    "message": "Configuration updated locally and propagated to neighbors"
                })

            return True

        except Exception as e:
            logger.error(f"Error configuring network: {e}")
            if callback:
                callback(False, {"status": "error", "message": f"Error configuring network: {e}"})
            return False

    def discover_neighbors(self, duration: int = 8) -> List[Tuple[str, str]]:
        """
        Discover potential neighbor nodes via Bluetooth scanning.

        Args:
            duration: Scan duration in seconds

        Returns:
            List of (MAC address, name) pairs
        """
        return self.bt_comm.scan_for_nodes(duration)

    def connect_to_neighbor(self, addr: str, name: str) -> bool:
        """
        Connect to a potential neighbor.

        Args:
            addr: Bluetooth MAC address
            name: Device name

        Returns:
            True if connection was successful or already exists
        """
        return self.bt_comm.connect_to_node(addr, name)

    def send_data_to_node(self, dest_mac: str, data: Dict[str, Any],
                          on_route_discovery: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Send data to a node, discovering route if needed.

        Args:
            dest_mac: Destination MAC address
            data: Data to send
            on_route_discovery: Optional callback when route discovery completes

        Returns:
            True if data was sent or route discovery started
        """
        try:
            # Check if directly connected
            with self.state_lock:
                if dest_mac in self.bt_comm.connected_devices:
                    # Directly connected, send immediately
                    return self.bt_comm.send_json(dest_mac, data)

            # Check if we have a route
            next_hop = self._find_next_hop_to_destination(dest_mac)

            if next_hop:
                # We have a route, send to next hop
                return self.bt_comm.send_json(next_hop, data)
            else:
                # No route, start discovery
                def route_callback(success, route):
                    if success and route:
                        # Route found, send data
                        next_hop = route[0]
                        try:
                            self.bt_comm.send_json(next_hop, data)
                            if on_route_discovery:
                                on_route_discovery(True)
                        except Exception as e:
                            logger.error(f"Failed to send data after route discovery: {e}")
                            if on_route_discovery:
                                on_route_discovery(False)
                    else:
                        # Route discovery failed
                        logger.error(f"Route discovery failed for {dest_mac}")
                        if on_route_discovery:
                            on_route_discovery(False)

                # Start route discovery
                self.discover_route("", dest_mac=dest_mac, callback=route_callback)
                return True

        except Exception as e:
            logger.error(f"Error sending data to node: {e}")
            return False

    def get_neighbors(self) -> List[Dict[str, Any]]:
        """
        Get list of known neighbors with their capabilities.

        Returns:
            List of neighbor information dictionaries
        """
        neighbors = []
        seen_macs = set()  # Track MACs we've already added

        with self.state_lock:
            for neighbor in self.aodv_state.neighbours:
                mac = neighbor.bt_mac_address
                if mac in seen_macs:
                    continue  # Skip duplicates

                seen_macs.add(mac)
                neighbor_info = {
                    "node_id": neighbor.node_id,
                    "bt_mac_address": mac,
                    "neighbors": neighbor.neighbors,
                    "capabilities": self.neighbor_capabilities.get(mac, {})
                }
                neighbors.append(neighbor_info)
        return neighbors

    def get_routes(self) -> List[Dict[str, Any]]:
        """
        Get list of known routes.

        Returns:
            List of route information dictionaries
        """
        routes = []
        with self.state_lock:
            for route in self.aodv_state.routing_table:
                hops = []
                for hop in route.hops:
                    hops.append({
                        "node_id": hop.node_id,
                        "bt_mac_address": hop.bt_mac_address
                    })

                routes.append({
                    "destination": {
                        "node_id": route.host.node_id,
                        "bt_mac_address": route.host.bt_mac_address
                    },
                    "hops": hops
                })
        return routes

    def get_network_topology(self) -> Dict[str, Any]:
        """
        Get the current network topology as seen by this node.
        Enhanced to fully utilize R_HELLO information.

        Returns:
            Dictionary representing the network topology
        """
        topology = {
            "nodes": [],
            "links": [],
            "last_updated": str(time.time())
        }

        with self.state_lock:
            # Add local node
            topology["nodes"].append({
                "id": self.node_id,
                "mac": self.mac_address,
                "is_local": True,
                "capabilities": self.capabilities
            })

            # Keep track of nodes and links we've already added
            added_nodes = {self.mac_address}
            added_links = set()  # Using (source, target) tuples

            # Helper function to add a link if it's not already present
            def add_link_if_new(source, target):
                # Skip self-loops (connections from a node to itself)
                if source == target:
                    return

                # Create a canonical representation of the link
                # (always using lexicographically smaller MAC as source)
                link_key = tuple(sorted([source, target]))
                if link_key not in added_links:
                    topology["links"].append({
                        "source": source,
                        "target": target
                    })
                    added_links.add(link_key)

            # Add neighbors as nodes and their links
            for neighbor in self.aodv_state.neighbours:
                mac = neighbor.bt_mac_address
                if mac not in added_nodes:
                    topology["nodes"].append({
                        "id": neighbor.node_id or mac,
                        "mac": mac,
                        "is_local": False,
                        "capabilities": self.node_capabilities.get(mac, {}) or self.neighbor_capabilities.get(mac, {})
                    })
                    added_nodes.add(mac)

                # Add link to neighbor
                add_link_if_new(self.mac_address, mac)

                # Add neighbor's neighbors from HELLO messages
                for neighbor_of_neighbor in neighbor.neighbors:
                    # Skip self-references in neighbor lists
                    if neighbor_of_neighbor == mac or neighbor_of_neighbor == self.mac_address:
                        continue

                    if neighbor_of_neighbor not in added_nodes:
                        # We don't have full info about this node, just the MAC
                        topology["nodes"].append({
                            "id": neighbor_of_neighbor,  # Use MAC as ID since we don't have node_id
                            "mac": neighbor_of_neighbor,
                            "is_local": False,
                            "capabilities": self.node_capabilities.get(neighbor_of_neighbor, {})
                        })
                        added_nodes.add(neighbor_of_neighbor)

                    # Add link between neighbor and its neighbor
                    add_link_if_new(mac, neighbor_of_neighbor)

            # Add information from routes (including HELLO-propagated routes)
            for route in self.aodv_state.routing_table:
                # Add destination if not already in nodes
                dest_mac = route.host.bt_mac_address
                if dest_mac not in added_nodes:
                    topology["nodes"].append({
                        "id": route.host.node_id or dest_mac,
                        "mac": dest_mac,
                        "is_local": False,
                        "capabilities": self.node_capabilities.get(dest_mac, {})
                    })
                    added_nodes.add(dest_mac)

                # Add links between hops
                prev_hop = self.mac_address  # Start from local node
                for hop in route.hops:
                    hop_mac = hop.bt_mac_address

                    # Add hop if not already in nodes
                    if hop_mac not in added_nodes:
                        topology["nodes"].append({
                            "id": hop.node_id or hop_mac,
                            "mac": hop_mac,
                            "is_local": False,
                            "capabilities": self.node_capabilities.get(hop_mac, {})
                        })
                        added_nodes.add(hop_mac)

                    # Add link from previous hop
                    add_link_if_new(prev_hop, hop_mac)

                    # Also add neighbors of this hop if known
                    for hop_neighbor in hop.neighbors:
                        # Skip self-references in neighbor lists
                        if hop_neighbor == hop_mac or hop_neighbor == self.mac_address:
                            continue

                        if hop_neighbor not in added_nodes:
                            topology["nodes"].append({
                                "id": hop_neighbor,
                                "mac": hop_neighbor,
                                "is_local": False,
                                "capabilities": self.node_capabilities.get(hop_neighbor, {})
                            })
                            added_nodes.add(hop_neighbor)

                        # Add link between hop and its neighbor
                        add_link_if_new(hop_mac, hop_neighbor)

                    prev_hop = hop_mac

                # If this is a direct route (no hops), add direct link to destination
                if not route.hops and dest_mac != self.mac_address:
                    add_link_if_new(self.mac_address, dest_mac)

                # Otherwise connect last hop to destination
                elif route.hops:
                    add_link_if_new(prev_hop, dest_mac)

        return topology

    def register_sensor(self, sensor):
        """
        Register a new sensor with the protocol

        Args:
            sensor: Sensor object implementing the Sensor interface
        """
        self.sensor_registry.register_sensor(sensor)
        # Update our local capabilities after registering a new sensor
        self.capabilities.update(self.sensor_registry.get_all_capabilities())
        logger.info(f"Registered sensor: {sensor.name}")

    def unregister_sensor(self, sensor_name: str):
        """
        Unregister a sensor from the protocol

        Args:
            sensor_name: Name of the sensor to unregister
        """
        self.sensor_registry.unregister_sensor(sensor_name)
        # Update our local capabilities after unregistering a sensor
        self.capabilities.update(self.sensor_registry.get_all_capabilities())
        logger.info(f"Unregistered sensor: {sensor_name}")

    def get_sensor_reading(self, sensor_name: str):
        """
        Get a reading from a specific sensor

        Args:
            sensor_name: Name of the sensor to read from

        Returns:
            Sensor reading or None if sensor not available
        """
        sensor = self.sensor_registry.get_sensor(sensor_name)
        if sensor and sensor.enabled:
            try:
                return sensor.read()
            except Exception as e:
                logger.error(f"Error reading {sensor_name} sensor: {e}")
        return None

    def query_remote_sensor(self, dest_mac: str, sensor_name: str,
                            callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query a sensor on a remote node

        Args:
            dest_mac: Destination MAC address
            sensor_name: Name of the sensor to query
            callback: Callback function to call when response is received

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        # Use the standardized sensor query format
        return self.query_node(dest_mac, f"sensor:{sensor_name}", callback)

    def get_all_sensors(self):
        """
        Get information about all registered sensors

        Returns:
            Dictionary of sensor info
        """
        return self.sensor_registry.get_all_sensors_info()

    def enable_sensor(self, sensor_name: str, enabled: bool = True):
        """
        Enable or disable a sensor

        Args:
            sensor_name: Name of the sensor to enable/disable
            enabled: True to enable, False to disable

        Returns:
            True if successful, False otherwise
        """
        sensor = self.sensor_registry.get_sensor(sensor_name)
        if sensor:
            sensor.enabled = enabled
            # Update capabilities
            self.capabilities.update(self.sensor_registry.get_all_capabilities())
            return True
        return False

    def query_sensor(self, dest_mac: str, sensor_name: str,
                     callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query a specific sensor on a remote node

        Args:
            dest_mac: Destination MAC address
            sensor_name: Name of the sensor to query (e.g., "temperature", "humidity")
            callback: Callback function to call when response is received

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        return self.query_node(dest_mac, f"sensor:{sensor_name}", callback)

    def query_capabilities(self, dest_mac: str,
                           callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query the capabilities of a remote node

        Args:
            dest_mac: Destination MAC address
            callback: Callback function to call when response is received

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        return self.query_node(dest_mac, "capabilities", callback)

    def query_neighbors(self, dest_mac: str,
                        callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query the neighbors of a remote node

        Args:
            dest_mac: Destination MAC address
            callback: Callback function to call when response is received

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        return self.query_node(dest_mac, "neighbors", callback)

    def query_network_config(self, dest_mac: str,
                             callback: Optional[Callable[[bool, Dict[str, Any]], None]] = None):
        """
        Query the network configuration of a remote node

        Args:
            dest_mac: Destination MAC address
            callback: Callback function to call when response is received

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        return self.query_node(dest_mac, "network_config", callback)

    def _send_query_request(self, request_data, dest_mac, next_hop, callback):
        """
        Helper method to send a query request to a destination

        Args:
            request_data: The query request data
            dest_mac: Destination MAC address
            next_hop: Next hop MAC address or None if unknown
            callback: Callback function

        Returns:
            broadcast_id: ID of the broadcast request or None if error
        """
        broadcast_id = request_data.get("broadcast_id")

        # If we have a next hop, send directly to it
        if next_hop:
            try:
                success = self.bt_comm.send_json(next_hop, request_data)
                if success:
                    logger.info(f"Sent query request to {dest_mac} via {next_hop}")
                    return broadcast_id
                else:
                    logger.error(f"Failed to send query to {next_hop}")
                    # Remove the callback since we couldn't send the request
                    with self.query_lock:
                        self.pending_queries.pop(broadcast_id, None)
                    if callback:
                        callback(False, {"status": "error", "message": f"Failed to send query"})
                    return None
            except Exception as e:
                logger.error(f"Failed to send query to {next_hop}: {e}")
                # Remove the callback since we couldn't send the request
                with self.query_lock:
                    self.pending_queries.pop(broadcast_id, None)
                if callback:
                    callback(False, {"status": "error", "message": f"Failed to send query: {e}"})
                return None
        else:
            # Broadcast to all connected devices
            with self.state_lock:
                connected_devices = list(self.bt_comm.connected_devices.keys())

            if not connected_devices:
                logger.error("No connected devices to send query")
                # Remove the callback since we couldn't send the request
                with self.query_lock:
                    self.pending_queries.pop(broadcast_id, None)
                if callback:
                    callback(False, {"status": "error", "message": "No connected devices to send query"})
                return None

            sent = False
            for device_addr in connected_devices:
                try:
                    success = self.bt_comm.send_json(device_addr, request_data)
                    if success:
                        sent = True
                        logger.info(f"Broadcast query to {device_addr}")
                except Exception as e:
                    logger.error(f"Failed to send query to {device_addr}: {e}")

            if sent:
                logger.info(f"Broadcast query request to {len(connected_devices)} devices")
                return broadcast_id
            else:
                logger.error("Failed to send query to any device")
                # Remove the callback since we couldn't send the request
                with self.query_lock:
                    self.pending_queries.pop(broadcast_id, None)
                if callback:
                    callback(False, {"status": "error", "message": "Failed to send query to any device"})
                return None

    def close(self):
        """Clean up resources"""
        logger.info("Shutting down E-AODV protocol")
        try:
            self.bt_comm.close()
        except Exception as e:
            logger.error(f"Error closing Bluetooth communication: {e}")


# Example usage
if __name__ == "__main__":
    import argparse
    import sys
    import signal

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="E-AODV Protocol Implementation")
    parser.add_argument("--node-id", type=str, required=True, help="Node identifier")
    parser.add_argument("--temp", action="store_true", help="Temperature sensor capability (default: enabled)")
    parser.add_argument("--no-temp", action="store_true", help="Disable temperature sensor capability")
    parser.add_argument("--humidity", action="store_true", help="Humidity sensor capability")
    parser.add_argument("--motion", action="store_true", help="Motion sensor capability")
    parser.add_argument("--led", action="store_true", help="LED capability")
    parser.add_argument("--motor", action="store_true", help="Motor capability")
    parser.add_argument("--display", action="store_true", help="Display capability")
    args = parser.parse_args()

    # Set capabilities based on arguments
    capabilities = {
        "temperature": not args.no_temp,  # Default enabled unless --no-temp is specified
        "humidity": args.humidity,
        "motion": args.motion,
        "led": args.led,
        "motor": args.motor,
        "display": args.display
    }

    # Create protocol instance
    eaodv = EAODVProtocol(node_id=args.node_id, capabilities=capabilities)


    # Register signal handler
    def signal_handler(sig, frame):
        print("\nShutting down...")
        eaodv.close()
        sys.exit(0)


    signal.signal(signal.SIGINT, signal_handler)

    # Main loop
    try:
        print(f"E-AODV Node {args.node_id} ({eaodv.mac_address}) started")
        print("Press Ctrl+C to exit")

        # Keep the main thread alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        eaodv.close()