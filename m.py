#!/usr/bin/env python3
"""
E-AODV Demo Application

This script provides a comprehensive terminal interface to test E-AODV protocol capabilities,
including node discovery, routing, data queries, data writes, and network configuration.
"""
import time
import logging
import sys
import threading
import json
import argparse
import os
import curses
from datetime import datetime
from typing import Dict, Any, List, Optional
import tempfile
import webbrowser
from sensors.sensor_registry import SensorRegistry
from sensors.sensor import Sensor
from sensors.cpu_temperature import CPUTemperatureSensor

from sensors.phy_sensors import initialize_sensors

# Import EAODV protocol
from eaodv_protocol import EAODVProtocol, OperationType

# Import visualization libraries
try:
    import networkx as nx
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    HAS_VISUALIZATION = True
except ImportError:
    HAS_VISUALIZATION = False
    print("Warning: networkx or matplotlib not installed. Network visualization will be disabled.")

# Configure logging
log_format = "%(asctime)s [%(levelname)s] %(message)s"
log_formatter = logging.Formatter(log_format)

# Create file handler
os.makedirs("logs", exist_ok=True)
file_handler = logging.FileHandler(f"logs/eaodv_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

# Create console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

# Setup logger
logger = logging.getLogger(__name__)
# logger.setLevel(logging.INFO)
# logger.addHandler(file_handler)
# logger.addHandler(console_handler)


class EAODVDemo:
    """
    Demonstration of E-AODV protocol usage with complete terminal interface
    """
    
    def __init__(self, node_id: str, capabilities: Dict[str, bool] = None):
        """
        Initialize the demo.
        
        Args:
            node_id: Node identifier
            capabilities: Dict of node capabilities
        """
        self.node_id = node_id
        
        # Set default capabilities if none provided
        if capabilities is None:
            capabilities = {
                "temperature": True,  # All nodes have temperature capability by default
            }
        
        self.sensor_registry = SensorRegistry()
        self.sensor_registry.register_sensor(CPUTemperatureSensor(True))
        initialize_sensors(self.sensor_registry, False)
        # Create protocol instance with capabilities
        self.eaodv = EAODVProtocol(
            node_id=node_id,
            auto_discovery=True,
            capabilities=capabilities,
            sensor_registry=self.sensor_registry
        )
        
        # Track discovered nodes
        self.discovered_nodes = {}
        self.discovery_lock = threading.Lock()
        
        # For storing logs to display in the UI
        self.log_messages = []
        self.max_log_messages = 100
        self.log_lock = threading.RLock()
        
        # Save original logging handlers
        self.original_handlers = logger.handlers.copy()
        
        # Create a capabilities string for display
        caps_str = ", ".join([cap for cap, enabled in capabilities.items() if enabled])
        logger.info(f"E-AODV Demo initialized with node ID: {node_id}")
        logger.info(f"Node capabilities: {caps_str}")
        
        # Setup custom logging
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup custom logging to capture logs for UI display"""
        # Remove existing UI handlers of the same type first
        for handler in list(logger.handlers):
            if hasattr(handler, 'demo') and handler.demo == self:
                logger.removeHandler(handler)
                
        class UILogHandler(logging.Handler):
            def __init__(self, demo):
                super().__init__()
                self.demo = demo
                
            def emit(self, record):
                log_entry = self.format(record)
                with self.demo.log_lock:
                    self.demo.log_messages.append(log_entry)
                    # Keep only the last N messages
                    if len(self.demo.log_messages) > self.demo.max_log_messages:
                        self.demo.log_messages = self.demo.log_messages[-self.demo.max_log_messages:]
        
        # Create and add our custom handler
        ui_handler = UILogHandler(self)
        ui_handler.setFormatter(log_formatter)
        logger.addHandler(ui_handler)

    def run(self):
        """
        Run the demo with a user-friendly terminal interface.
        """
        logger.info("Starting E-AODV demo")

        # Menu loop
        while True:
            self._clear_screen()
            self._print_header()
            self._print_menu()
            self._print_log_window()

            try:
                choice = input("\nEnter choice: ")

                if choice == "1":
                    self.discover_nodes()
                elif choice == "2":
                    self.connect_to_node()
                elif choice == "3":
                    self.show_connected_nodes()
                elif choice == "4":
                    self.find_route()
                elif choice == "5":
                    self.send_message()
                elif choice == "6":
                    self.show_topology()
                elif choice == "7":
                    self.show_routing_table()
                elif choice == "8":
                    self.query_node()
                elif choice == "9":
                    self.write_to_node()
                elif choice == "10":
                    self.configure_network()
                elif choice == "11":
                    self.visualize_network()  # New visualization option
                elif choice == "12":  # Updated quit option number
                    self.quit()
                    break
                else:
                    print("Invalid choice, please try again.")
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nExiting...")
                self.quit()
                break
            except Exception as e:
                logger.error(f"Error in menu selection: {e}")
                input("Press Enter to continue...")
    
    def _clear_screen(self):
        """Clear the terminal screen"""
        os.system('cls' if os.name == 'nt' else 'clear')
    
    def _print_header(self):
        """Print a nice header with node information"""
        header = f"=== E-AODV Demo - Node: {self.node_id} ({self.eaodv.mac_address}) ==="
        border = "=" * len(header)
        
        # Get capabilities as readable string
        capabilities = []
        for cap, enabled in self.eaodv.capabilities.items():
            if enabled and cap != "temperature_value":
                capabilities.append(cap)
        
        caps_str = ", ".join(capabilities) if capabilities else "None"
        
        print(border)
        print(header)
        print(f"Capabilities: {caps_str}")
        print(border)
    
    def _print_menu(self):
        """Print the menu options"""
        print("\nMenu Options:")
        print("1. Discover nearby nodes")
        print("2. Connect to discovered node")
        print("3. Show connected nodes")
        print("4. Find route to node")
        print("5. Send message to node")
        print("6. Show network topology")
        print("7. Show routing table")
        print("8. Query node data")
        print("9. Write data to node")
        print("10. Configure network")
        print("11. Create network diagram")
        print("12. Quit")
    
    def _print_log_window(self):
        """Print recent log messages in a scrollable window"""
        print("\nRecent Activity:")
        print("-" * 80)
        
        with self.log_lock:
            # Display the most recent logs (last 5)
            logs_to_show = self.log_messages[-5:] if self.log_messages else ["No activity yet"]
            
            for log in logs_to_show:
                # Truncate log if it's too long
                if len(log) > 79:
                    print(log[:76] + "...")
                else:
                    print(log)
        
        print("-" * 80)
    
    def discover_nodes(self):
        """
        Discover nearby nodes.
        """
        self._clear_screen()
        print("\n=== Node Discovery ===")
        print("Scanning for nearby nodes...")
        
        # Scan for nodes
        devices = self.eaodv.discover_neighbors(duration=8)
        
        if not devices:
            print("No devices found.")
            input("\nPress Enter to continue...")
            return
        
        # Store discovered devices
        with self.discovery_lock:
            self.discovered_nodes.clear()
            for i, (addr, name) in enumerate(devices):
                self.discovered_nodes[str(i+1)] = {
                    "addr": addr,
                    "name": name
                }
        
        # Display discovered devices
        print("\nDiscovered devices:")
        for idx, device in self.discovered_nodes.items():
            print(f"{idx}. {device['name']} ({device['addr']})")
        
        input("\nPress Enter to continue...")
    
    def connect_to_node(self):
        """
        Connect to a discovered node.
        """
        self._clear_screen()
        print("\n=== Connect to Node ===")
        
        if not self.discovered_nodes:
            print("No discovered nodes. Run discovery first.")
            input("\nPress Enter to continue...")
            return
        
        # Display discovered devices
        print("\nDiscovered devices:")
        for idx, device in self.discovered_nodes.items():
            print(f"{idx}. {device['name']} ({device['addr']})")
        
        # Get device selection
        choice = input("\nEnter device number to connect to (or 'c' to cancel): ")
        
        if choice.lower() == 'c':
            return
        
        with self.discovery_lock:
            if choice not in self.discovered_nodes:
                print("Invalid choice.")
                input("\nPress Enter to continue...")
                return
            
            device = self.discovered_nodes[choice]
        
        # Connect to device
        print(f"Connecting to {device['name']} ({device['addr']})...")
        result = self.eaodv.connect_to_neighbor(device['addr'], device['name'])
        
        if result:
            print(f"Successfully connected to {device['name']}!")
        else:
            print(f"Failed to connect to {device['name']}.")
            
        input("\nPress Enter to continue...")
    
    def show_connected_nodes(self):
        """
        Show connected nodes and their capabilities.
        """
        self._clear_screen()
        print("\n=== Connected Nodes ===")
        
        neighbors = self.eaodv.get_neighbors()
        
        if not neighbors:
            print("No connected nodes.")
            input("\nPress Enter to continue...")
            return
        
        print("\nConnected nodes:")
        for i, neighbor in enumerate(neighbors):
            node_id = neighbor.get("node_id") or "Unknown"
            mac = neighbor.get("bt_mac_address", "Unknown")
            
            # Display capabilities
            capabilities = neighbor.get("capabilities", {})
            cap_list = []
            
            # Check for temperature capability
            if "temperature" in capabilities and capabilities["temperature"]:
                temp_value = capabilities.get("temperature_value", "N/A")
                cap_list.append(f"Temperature: {temp_value}°C")
            
            # Add other capabilities
            for cap, enabled in capabilities.items():
                if cap not in ["temperature", "temperature_value"] and enabled:
                    cap_list.append(cap)
            
            caps_str = ", ".join(cap_list) if cap_list else "No known capabilities"
            print(f"{i+1}. {node_id} ({mac}) - {caps_str}")
        
        input("\nPress Enter to continue...")
    
    def find_route(self):
        """
        Find a route to a node.
        """
        self._clear_screen()
        print("\n=== Find Route to Node ===")
        
        # Get destination node
        dest_mac = input("\nEnter destination MAC address: ")
        dest_id = input("Enter destination node ID (optional): ")
        
        if not dest_mac:
            print("MAC address is required.")
            input("\nPress Enter to continue...")
            return
        
        # Set up discovery callback
        route_event = threading.Event()
        route_result = {"success": False, "route": None}
        
        def route_callback(success, route):
            route_result["success"] = success
            route_result["route"] = route
            route_event.set()
        
        # Start route discovery
        print(f"Finding route to {dest_id or dest_mac}...")
        self.eaodv.discover_route(
            dest_id=dest_id or "",
            dest_mac=dest_mac,
            callback=route_callback
        )
        
        # Wait for result with timeout
        route_event.wait(timeout=15)
        
        if route_result["success"]:
            print(f"Route found: {' -> '.join(route_result['route'])}")
        else:
            print("Failed to find route.")
            
        input("\nPress Enter to continue...")
    
    def send_message(self):
        """
        Send a message to a node.
        """
        self._clear_screen()
        print("\n=== Send Message ===")
        
        # Get destination node
        dest_mac = input("\nEnter destination MAC address: ")
        message = input("Enter message to send: ")
        
        if not dest_mac or not message:
            print("Both MAC address and message are required.")
            input("\nPress Enter to continue...")
            return
        
        # Create message data
        message_data = {
            "type": "user_message",
            "source_id": self.node_id,
            "source_mac": self.eaodv.mac_address,
            "message": message,
            "timestamp": str(time.time())
        }
        
        # Set up result callback
        result_event = threading.Event()
        send_result = {"success": False}
        
        def route_callback(success):
            send_result["success"] = success
            result_event.set()
        
        # Send message
        print(f"Sending message to {dest_mac}...")
        self.eaodv.send_data_to_node(
            dest_mac=dest_mac,
            data=message_data,
            on_route_discovery=route_callback
        )
        
        # Wait for result with timeout
        result_event.wait(timeout=15)
        
        if send_result["success"]:
            print("Message sent successfully!")
        else:
            print("Failed to send message.")
            
        input("\nPress Enter to continue...")
    
    def show_topology(self):
        """
        Show network topology.
        """
        self._clear_screen()
        print("\n=== Network Topology ===")
        
        topology = self.eaodv.get_network_topology()
        
        print("\nNodes:")
        for node in topology["nodes"]:
            node_id = node.get("id") or "Unknown"
            mac = node.get("mac", "Unknown")
            is_local = node.get("is_local", False)
            print(f"  {node_id} ({mac}){' (local)' if is_local else ''}")
        
        print("\nLinks:")
        for link in topology["links"]:
            source = link.get("source", "Unknown")
            target = link.get("target", "Unknown")
            print(f"  {source} <-> {target}")
            
        input("\nPress Enter to continue...")
    
    def show_routing_table(self):
        """
        Show routing table.
        """
        self._clear_screen()
        print("\n=== Routing Table ===")
        
        routes = self.eaodv.get_routes()
        
        if not routes:
            print("Routing table is empty.")
            input("\nPress Enter to continue...")
            return
        
        print("\nRouting Table:")
        for i, route in enumerate(routes):
            dest = route.get("destination", {})
            dest_id = dest.get("node_id") or "Unknown"
            dest_mac = dest.get("bt_mac_address", "Unknown")
            
            hops = route.get("hops", [])
            hop_str = " -> ".join([hop.get("bt_mac_address", "?") for hop in hops])
            
            print(f"{i+1}. To: {dest_id} ({dest_mac})")
            print(f"   Via: {hop_str}")
            
        input("\nPress Enter to continue...")

    def query_node(self):
        """
        Query data from a remote node with enhanced node selection from entire network topology.
        """
        self._clear_screen()
        print("\n=== Query Node Data ===")

        # First determine if we want to select from known nodes or enter a MAC address
        print("\nChoose node selection method:")
        print("1. Select from known nodes (direct + topology)")
        print("2. Enter MAC address manually")

        selection_method = input("\nEnter choice (1-2): ")
        dest_mac = None

        if selection_method == "1":
            # Get all known nodes from both direct connections and topology
            directly_connected_nodes = self.eaodv.get_neighbors()
            topology = self.eaodv.get_network_topology()

            # Create a combined list of all known nodes
            all_nodes = []

            # Track MACs to avoid duplicates
            known_macs = set()

            # First add directly connected nodes
            for node in directly_connected_nodes:
                mac = node.get("bt_mac_address", "")
                if mac and mac not in known_macs:
                    node["connection_type"] = "direct"  # Add connection type
                    all_nodes.append(node)
                    known_macs.add(mac)

            # Then add nodes from topology that aren't directly connected
            for node in topology["nodes"]:
                mac = node.get("mac", "")
                if mac and mac not in known_macs and not node.get("is_local", False):
                    # Convert topology node format to match directly connected node format
                    topology_node = {
                        "node_id": node.get("id", "Unknown"),
                        "bt_mac_address": mac,
                        "connection_type": "topology",  # Mark as known through topology
                        "capabilities": node.get("capabilities", {})
                    }
                    all_nodes.append(topology_node)
                    known_macs.add(mac)

            if not all_nodes:
                print("No nodes available in the network.")
                input("\nPress Enter to continue...")
                return

            print("\nAvailable nodes in network:")
            for i, node in enumerate(all_nodes):
                node_id = node.get("node_id", "Unknown")
                mac = node.get("bt_mac_address", "")
                connection_type = node.get("connection_type", "unknown")

                # Show capabilities if available
                caps = []
                if "capabilities" in node:
                    for cap, enabled in node["capabilities"].items():
                        if cap != "temperature_value" and not cap.endswith("_writable") and enabled:
                            caps.append(cap)
                    cap_str = f" - Capabilities: {', '.join(caps)}" if caps else ""

                    # Display with connection type indicator
                    conn_indicator = "(direct)" if connection_type == "direct" else "(via topology)"
                    print(f"{i + 1}. {node_id} ({mac}) {conn_indicator}{cap_str}")
                else:
                    conn_indicator = "(direct)" if connection_type == "direct" else "(via topology)"
                    print(f"{i + 1}. {node_id} ({mac}) {conn_indicator}")

            try:
                choice = int(input("\nSelect node (number): "))
                if choice < 1 or choice > len(all_nodes):
                    print("Invalid selection.")
                    input("\nPress Enter to continue...")
                    return

                dest_mac = all_nodes[choice - 1]["bt_mac_address"]
                # Get node_id for better logging
                selected_node_id = all_nodes[choice - 1].get("node_id", "")
                conn_type = all_nodes[choice - 1].get("connection_type", "")

                if conn_type == "topology":
                    print(f"Selected node: {selected_node_id} ({dest_mac}) - Note: This node is known through topology")
                else:
                    print(f"Selected node: {selected_node_id} ({dest_mac})")
            except ValueError:
                print("Invalid input. Please enter a number.")
                input("\nPress Enter to continue...")
                return
        else:
            # Manual MAC address entry
            dest_mac = input("\nEnter destination MAC address: ")

        if not dest_mac:
            print("No destination selected.")
            input("\nPress Enter to continue...")
            return

        # Rest of the existing query_node method continues as before...
        # Query for capabilities first to show appropriate options
        print(f"Querying capabilities of {dest_mac}...")

        # Set up query callback
        caps_event = threading.Event()
        caps_result = {"success": False, "data": None}

        def caps_callback(success, data):
            caps_result["success"] = success
            caps_result["data"] = data
            caps_event.set()

        # First query capabilities
        self.eaodv.query_node(
            dest_mac=dest_mac,
            query_type="capabilities",
            callback=caps_callback
        )

        # Wait for capabilities result with timeout
        caps_event.wait(timeout=10)

        # Show query options
        print("\nQuery types:")
        query_options = []
        option_num = 1

        # Add available sensors based on capabilities
        if caps_result["success"] and "capabilities" in caps_result["data"]:
            caps = caps_result["data"]["capabilities"]

            # First pass: Find all sensor capabilities
            sensor_capabilities = set()
            for cap, enabled in caps.items():
                # Skip metadata fields and disabled capabilities
                if cap.endswith("_writable") or cap.endswith("_value") or not enabled:
                    continue
                sensor_capabilities.add(cap)

            # Add each sensor capability as a query option
            for sensor in sorted(sensor_capabilities):
                print(f"{option_num}. {sensor.title()}")
                query_options.append(f"sensor:{sensor}")
                option_num += 1

        # Add standard query types
        print(f"{option_num}. Node Capabilities")
        query_options.append("capabilities")
        option_num += 1

        print(f"{option_num}. Network Configuration")
        query_options.append("network_config")
        option_num += 1

        print(f"{option_num}. Neighbor List")
        query_options.append("neighbors")
        option_num += 1

        try:
            query_choice = int(input("\nSelect query type (number): "))
            if query_choice < 1 or query_choice > len(query_options):
                print("Invalid query type.")
                input("\nPress Enter to continue...")
                return

            query_type = query_options[query_choice - 1]
        except ValueError:
            print("Invalid input. Please enter a number.")
            input("\nPress Enter to continue...")
            return

        # Set up query callback
        query_event = threading.Event()
        query_result = {"success": False, "data": None}

        def query_callback(success, data):
            query_result["success"] = success
            query_result["data"] = data
            query_event.set()

        # Send query
        print(f"Querying {query_type} from {dest_mac}...")
        self.eaodv.query_node(
            dest_mac=dest_mac,
            query_type=query_type,
            callback=query_callback
        )

        # Wait for result with timeout
        query_event.wait(timeout=15)

        # Process the query result
        if query_result["success"]:
            print("\nQuery successful!")
            print("Response data:")

            # Format response for different query types
            data = query_result["data"]

            if query_type.startswith("sensor:"):
                # Extract the actual sensor name from the query type
                sensor_name = query_type.split(":", 1)[1]

                if sensor_name in data:
                    print(f"{sensor_name.title()}: {data[sensor_name]}")
                else:
                    print(f"{sensor_name.title()} data not available")

            elif query_type == "capabilities":
                if "capabilities" in data:
                    caps = data["capabilities"]
                    # First show sensor values
                    for cap, value in caps.items():
                        if cap.endswith("_value"):
                            sensor_name = cap.replace("_value", "")
                            print(f"- {sensor_name.title()}: {value}")

                    # Then show enabled/disabled sensors
                    for cap, enabled in caps.items():
                        if not cap.endswith("_value") and not cap.endswith("_writable"):
                            status = "Enabled" if enabled else "Disabled"
                            print(f"- {cap.title()}: {status}")

                    # Finally show writable capabilities
                    for cap, enabled in caps.items():
                        if cap.endswith("_writable") and enabled:
                            sensor_name = cap.replace("_writable", "")
                            print(f"- {sensor_name.title()}: Writable")
                else:
                    print("Capabilities data not available")

            elif query_type == "network_config":
                for key, value in data.items():
                    if key != "status" and key != "message":
                        print(f"- {key}: {value}")

            elif query_type == "neighbors":
                if "neighbors" in data:
                    neighbors = data["neighbors"]
                    if neighbors:
                        for i, neighbor in enumerate(neighbors):
                            node_id = neighbor.get("node_id", "Unknown")
                            mac = neighbor.get("bt_mac_address", "Unknown")
                            print(f"{i + 1}. {node_id} ({mac})")
                    else:
                        print("No neighbors reported")
                else:
                    print("Neighbor data not available")
            else:
                # Generic handler for any other type of data
                for key, value in data.items():
                    if key != "status" and key != "message":
                        print(f"- {key}: {value}")
        else:
            print("Query failed.")
            if "message" in query_result.get("data", {}):
                print(f"Error: {query_result['data']['message']}")

        input("\nPress Enter to continue...")

    def write_to_node(self):
        """
        Write data to a remote node with enhanced node selection from entire topology.
        """
        self._clear_screen()
        print("\n=== Write to Node ===")

        # First determine if we want to select from known nodes or enter a MAC address
        print("\nChoose node selection method:")
        print("1. Select from known nodes (direct + topology)")
        print("2. Enter MAC address manually")

        selection_method = input("\nEnter choice (1-2): ")
        dest_mac = None

        if selection_method == "1":
            # Get all known nodes from both direct connections and topology
            directly_connected_nodes = self.eaodv.get_neighbors()
            topology = self.eaodv.get_network_topology()

            # Create a combined list of all known nodes
            all_nodes = []

            # Track MACs to avoid duplicates
            known_macs = set()

            # First add directly connected nodes
            for node in directly_connected_nodes:
                mac = node.get("bt_mac_address", "")
                if mac and mac not in known_macs:
                    node["connection_type"] = "direct"  # Add connection type
                    all_nodes.append(node)
                    known_macs.add(mac)

            # Then add nodes from topology that aren't directly connected
            for node in topology["nodes"]:
                mac = node.get("mac", "")
                if mac and mac not in known_macs and not node.get("is_local", False):
                    # Convert topology node format to match directly connected node format
                    topology_node = {
                        "node_id": node.get("id", "Unknown"),
                        "bt_mac_address": mac,
                        "connection_type": "topology",  # Mark as known through topology
                        "capabilities": node.get("capabilities", {})
                    }
                    all_nodes.append(topology_node)
                    known_macs.add(mac)

            if not all_nodes:
                print("No nodes available in the network.")
                input("\nPress Enter to continue...")
                return

            print("\nAvailable nodes in network:")
            for i, node in enumerate(all_nodes):
                node_id = node.get("node_id", "Unknown")
                mac = node.get("bt_mac_address", "")
                connection_type = node.get("connection_type", "unknown")

                # Check for writable capabilities
                has_writable = False
                if "capabilities" in node:
                    for cap, enabled in node["capabilities"].items():
                        if cap.endswith("_writable") and enabled:
                            has_writable = True
                            break

                # Display with connection type indicator and writable flag
                conn_indicator = "(direct)" if connection_type == "direct" else "(via topology)"
                writable_indicator = " [writable]" if has_writable else ""
                print(f"{i + 1}. {node_id} ({mac}) {conn_indicator}{writable_indicator}")

            try:
                choice = int(input("\nSelect node (number): "))
                if choice < 1 or choice > len(all_nodes):
                    print("Invalid selection.")
                    input("\nPress Enter to continue...")
                    return

                dest_mac = all_nodes[choice - 1]["bt_mac_address"]
                selected_node_id = all_nodes[choice - 1].get("node_id", "")
                conn_type = all_nodes[choice - 1].get("connection_type", "")

                if conn_type == "topology":
                    print(f"Selected node: {selected_node_id} ({dest_mac}) - Note: This node is known through topology")
                else:
                    print(f"Selected node: {selected_node_id} ({dest_mac})")
            except ValueError:
                print("Invalid input. Please enter a number.")
                input("\nPress Enter to continue...")
                return
        else:
            # Manual MAC address entry
            dest_mac = input("\nEnter destination MAC address: ")

        if not dest_mac:
            print("No destination selected.")
            input("\nPress Enter to continue...")
            return

        # FIRST: Query the node's capabilities to see what's writable
        print(f"\nQuerying {dest_mac} capabilities...")

        # Set up query callback
        query_event = threading.Event()
        query_result = {"success": False, "data": None}

        def query_callback(success, data):
            query_result["success"] = success
            query_result["data"] = data
            query_event.set()

        # First query capabilities to get writable sensors
        self.eaodv.query_node(
            dest_mac=dest_mac,
            query_type="capabilities",
            callback=query_callback
        )

        # Wait for result with timeout
        query_event.wait(timeout=10)

        # Find writable sensors
        writable_sensors = []
        if query_result["success"] and "capabilities" in query_result["data"]:
            caps = query_result["data"]["capabilities"]
            # Look for _writable suffix in capabilities
            for cap, enabled in caps.items():
                if cap.endswith("_writable") and enabled:
                    sensor_name = cap.replace("_writable", "")
                    writable_sensors.append(sensor_name)

        # Show write options
        print("\nAvailable write options:")
        option_num = 1
        sensor_options = []

        # First show all detected writable sensors
        for sensor in writable_sensors:
            print(f"{option_num}. {sensor.title()} Sensor")
            sensor_options.append(sensor)
            option_num += 1

        if not sensor_options:
            print("No writable sensors or actuators found on the target node.")
            input("\nPress Enter to continue...")
            return

        try:
            write_choice = int(input("\nSelect option (number): "))
            if write_choice < 1 or write_choice > len(sensor_options):
                print("Invalid selection.")
                input("\nPress Enter to continue...")
                return

            selected_sensor = sensor_options[write_choice - 1]
        except ValueError:
            print("Invalid input. Please enter a number.")
            input("\nPress Enter to continue...")
            return

        # Handle value input based on sensor type
        write_data = {}

        if selected_sensor == "led":
            led_state = input("Enter LED state (on/off): ").lower()
            if led_state not in ["on", "off"]:
                print("Invalid LED state. Must be 'on' or 'off'.")
                input("\nPress Enter to continue...")
                return
            write_data[selected_sensor] = (led_state == "on")
        elif selected_sensor == "motor":
            try:
                motor_speed = int(input("Enter motor speed (0-100): "))
                if motor_speed < 0 or motor_speed > 100:
                    raise ValueError("Motor speed must be between 0 and 100")
                write_data[selected_sensor] = motor_speed
            except ValueError as e:
                print(f"Invalid motor speed: {e}")
                input("\nPress Enter to continue...")
                return
        elif selected_sensor == "display":
            display_message = input("Enter display message: ")
            write_data[selected_sensor] = display_message
        elif selected_sensor == "servo":
            try:
                angle = int(input("Enter servo angle (0-180): "))
                if angle < 0 or angle > 180:
                    raise ValueError("Servo angle must be between 0 and 180")
                write_data[selected_sensor] = angle
            except ValueError as e:
                print(f"Invalid servo angle: {e}")
                input("\nPress Enter to continue...")
                return
        else:
            # Generic sensor handling for unknown types
            value = input(f"Enter value for {selected_sensor}: ")

            # Try to convert to appropriate type
            try:
                # First try to convert to number if it looks like one
                if value.isdigit():
                    value = int(value)
                elif value.replace('.', '', 1).isdigit() and value.count('.') <= 1:
                    value = float(value)
                elif value.lower() in ['true', 'false', 'on', 'off', 'yes', 'no']:
                    value = value.lower() in ['true', 'on', 'yes']
            except:
                # If conversion fails, keep as string
                pass

            write_data[selected_sensor] = value

        # Set up write callback
        write_event = threading.Event()
        write_result = {"success": False, "data": None}

        def write_callback(success, data):
            write_result["success"] = success
            write_result["data"] = data
            write_event.set()

        # Send write request
        print(f"Writing data to {dest_mac}...")
        self.eaodv.write_to_node(
            dest_mac=dest_mac,
            write_data=write_data,
            callback=write_callback
        )

        # Wait for result with timeout
        write_event.wait(timeout=15)

        if write_result["success"]:
            print("\nWrite successful!")
            data = write_result["data"]
            if "updated_keys" in data:
                updated = ", ".join(data["updated_keys"])
                print(f"Updated values: {updated}")
        else:
            print("Write failed.")
            if "message" in write_result.get("data", {}):
                print(f"Error: {write_result['data']['message']}")

        input("\nPress Enter to continue...")
    
    def configure_network(self):
        """
        Configure network parameters.
        """
        self._clear_screen()
        print("\n=== Configure Network ===")
        
        print("Current network configuration:")
        print(f"- Hello interval: {self.eaodv.network_config.hello_interval} seconds")
        print(f"- Include sensor data in hello: {'Yes' if self.eaodv.network_config.include_sensor_data else 'No'}")
        print(f"- Default TTL for requests: {self.eaodv.network_config.ttl_default}")
        print(f"- Route cache timeout: {self.eaodv.network_config.route_cache_timeout} seconds")
        
        print("\nUpdate configuration (leave blank to keep current value):")
        
        config_params = {}
        
        # Get hello interval
        hello_interval_str = input("Hello interval (seconds): ")
        if hello_interval_str:
            try:
                hello_interval = int(hello_interval_str)
                if hello_interval <= 0:
                    raise ValueError("Hello interval must be positive")
                config_params["hello_interval"] = hello_interval
            except ValueError as e:
                print(f"Invalid hello interval: {e}")
                input("\nPress Enter to continue...")
                return
        
        # Get include sensor data flag
        include_sensor_str = input("Include sensor data in hello (y/n): ")
        if include_sensor_str:
            if include_sensor_str.lower() in ["y", "yes"]:
                config_params["include_sensor_data"] = True
            elif include_sensor_str.lower() in ["n", "no"]:
                config_params["include_sensor_data"] = False
            else:
                print("Invalid input. Must be 'y' or 'n'.")
                input("\nPress Enter to continue...")
                return
        
        # Get default TTL
        ttl_str = input("Default TTL for requests: ")
        if ttl_str:
            try:
                ttl = int(ttl_str)
                if ttl <= 0:
                    raise ValueError("TTL must be positive")
                config_params["ttl_default"] = ttl
            except ValueError as e:
                print(f"Invalid TTL: {e}")
                input("\nPress Enter to continue...")
                return
        
        # Get route cache timeout
        timeout_str = input("Route cache timeout (seconds): ")
        if timeout_str:
            try:
                timeout = int(timeout_str)
                if timeout <= 0:
                    raise ValueError("Timeout must be positive")
                config_params["route_cache_timeout"] = timeout
            except ValueError as e:
                print(f"Invalid timeout: {e}")
                input("\nPress Enter to continue...")
                return
        
        if not config_params:
            print("No changes made.")
            input("\nPress Enter to continue...")
            return
        
        # Confirm propagation
        propagate_str = input("Propagate changes to all nodes? (y/n): ")
        propagate = propagate_str.lower() in ["y", "yes"]
        
        # Set up configuration callback
        config_event = threading.Event()
        config_result = {"success": False, "data": None}
        
        def config_callback(success, data):
            config_result["success"] = success
            config_result["data"] = data
            config_event.set()
        
        # Update configuration
        print("Updating network configuration...")
        
        # If propagating, use the protocol's network configuration
        if propagate:
            self.eaodv.configure_network(
                config_params=config_params,
                callback=config_callback
            )
            
            # Wait for result with timeout
            config_event.wait(timeout=5)
            
            if config_result["success"]:
                print("\nConfiguration updated and propagated to network!")
                if "local_updates" in config_result.get("data", {}):
                    updated = ", ".join(config_result["data"]["local_updates"])
                    print(f"Updated parameters: {updated}")
            else:
                print("Configuration update failed.")
                if "message" in config_result.get("data", {}):
                    print(f"Error: {config_result['data']['message']}")
        else:
            # Just update local configuration
            if "hello_interval" in config_params:
                self.eaodv.network_config.hello_interval = config_params["hello_interval"]
                print(f"Updated hello_interval to {config_params['hello_interval']}")
            
            if "include_sensor_data" in config_params:
                self.eaodv.network_config.include_sensor_data = config_params["include_sensor_data"]
                print(f"Updated include_sensor_data to {config_params['include_sensor_data']}")
            
            if "ttl_default" in config_params:
                self.eaodv.network_config.ttl_default = config_params["ttl_default"]
                print(f"Updated ttl_default to {config_params['ttl_default']}")
            
            if "route_cache_timeout" in config_params:
                self.eaodv.network_config.route_cache_timeout = config_params["route_cache_timeout"]
                print(f"Updated route_cache_timeout to {config_params['route_cache_timeout']}")
            
            print("\nLocal configuration updated successfully!")
            
        input("\nPress Enter to continue...")
    
    def quit(self):
        """
        Clean up and quit the demo.
        """
        print("Shutting down E-AODV demo...")
        self.eaodv.close()
        
        # Restore original logging handlers
        logger.handlers = self.original_handlers
        
        print("Goodbye!")

    def visualize_network(self):
        """
        Generate and display a network topology visualization with capability information.
        """
        if not HAS_VISUALIZATION:
            self._clear_screen()
            print("\n=== Network Visualization ===")
            print("\nThis feature requires networkx and matplotlib libraries.")
            print("Please install them using: pip install networkx matplotlib")
            input("\nPress Enter to continue...")
            return

        self._clear_screen()
        print("\n=== Network Visualization ===")
        print("Generating enhanced network topology visualization with capabilities...")

        # Get the network topology
        topology = self.eaodv.get_network_topology()

        if not topology["nodes"]:
            print("No network topology data available.")
            input("\nPress Enter to continue...")
            return

        try:
            # Create a networkx graph
            G = nx.Graph()

            # Track node capabilities for visualization
            node_capabilities = {}

            # Add nodes with capability information
            for node in topology["nodes"]:
                node_id = node.get("id") or node.get("mac", "Unknown")
                is_local = node.get("is_local", False)
                capabilities = node.get("capabilities", {})

                # Extract key capabilities for display
                sensor_types = []
                writable_types = []

                for cap, enabled in capabilities.items():
                    if cap.endswith("_writable") and enabled:
                        # This is a writable capability
                        sensor_name = cap.replace("_writable", "")
                        writable_types.append(sensor_name)
                    elif not cap.endswith("_value") and not cap.endswith("_writable") and enabled:
                        # This is a regular sensor/capability
                        sensor_types.append(cap)

                # Store capabilities for node
                node_capabilities[node_id] = {
                    "sensors": sensor_types,
                    "writable": writable_types,
                    "total": len(sensor_types) + len(writable_types)
                }

                # Set node attributes including capabilities
                attrs = {
                    "mac": node.get("mac", ""),
                    "is_local": is_local,
                    "sensors": sensor_types,
                    "writable": writable_types,
                    "capabilities": capabilities
                }

                # Add the node
                G.add_node(node_id, **attrs)

            # Add links
            for link in topology["links"]:
                source = link.get("source")
                target = link.get("target")

                # Try to find more descriptive node IDs if available
                source_id = source
                target_id = target
                for node in topology["nodes"]:
                    if node.get("mac") == source:
                        source_id = node.get("id") or source
                    if node.get("mac") == target:
                        target_id = node.get("id") or target

                G.add_edge(source_id, target_id)

            # Create the figure
            plt.figure(figsize=(12, 10))

            # Get positions (layout)
            pos = nx.spring_layout(G, seed=42)  # Fixed seed for consistent layouts

            # Prepare node visualization attributes based on capabilities
            node_colors = []
            node_sizes = []
            node_shapes = []

            for node in G.nodes():
                # Base size on number of capabilities
                num_capabilities = node_capabilities[node].get("total", 0)
                base_size = 500 + (num_capabilities * 50)  # Larger nodes have more capabilities

                if G.nodes[node].get("is_local", False):
                    node_colors.append('red')  # Local node is red
                    node_sizes.append(base_size * 1.2)  # And larger
                    node_shapes.append('o')  # Circle for local node
                elif len(G.nodes[node].get("writable", [])) > 0:
                    # Nodes with writable capabilities
                    node_colors.append('green')  # Writable nodes are green
                    node_sizes.append(base_size)
                    node_shapes.append('s')  # Square for writable nodes
                elif len(G.nodes[node].get("sensors", [])) > 0:
                    # Nodes with sensors but not writable
                    node_colors.append('skyblue')
                    node_sizes.append(base_size)
                    node_shapes.append('o')  # Circle for sensor nodes
                else:
                    # Nodes without known capabilities
                    node_colors.append('lightgray')
                    node_sizes.append(base_size * 0.8)  # Smaller for unknown capability nodes
                    node_shapes.append('o')

            # Draw nodes for each shape type separately
            for shape in set(node_shapes):
                # Filter nodes by shape
                shape_nodes = [node for i, node in enumerate(G.nodes()) if node_shapes[i] == shape]
                if not shape_nodes:
                    continue

                # Get attributes for these nodes
                node_indices = [list(G.nodes()).index(node) for node in shape_nodes]
                shape_colors = [node_colors[i] for i in node_indices]
                shape_sizes = [node_sizes[i] for i in node_indices]

                # Draw this shape group
                nx.draw_networkx_nodes(G, pos,
                                       nodelist=shape_nodes,
                                       node_color=shape_colors,
                                       node_size=shape_sizes,
                                       node_shape=shape)

            # Draw edges
            nx.draw_networkx_edges(G, pos, width=2, alpha=0.7, edge_color='gray')

            # Draw labels with capability counts
            custom_labels = {}
            for node in G.nodes():
                sensors = G.nodes[node].get("sensors", [])
                writable = G.nodes[node].get("writable", [])
                if sensors or writable:
                    if writable:
                        custom_labels[node] = f"{node}\n({len(sensors)}S, {len(writable)}W)"
                    else:
                        custom_labels[node] = f"{node}\n({len(sensors)}S)"
                else:
                    custom_labels[node] = node

            nx.draw_networkx_labels(G, pos, labels=custom_labels, font_size=10, font_family='sans-serif')

            # Add legend
            plt.plot([], [], 'ro', markersize=10, label='Local Node')
            plt.plot([], [], 'o', color='skyblue', markersize=10, label='Sensor Node')
            plt.plot([], [], 's', color='green', markersize=10, label='Writable Node')
            plt.plot([], [], 'o', color='lightgray', markersize=10, label='Unknown Capabilities')
            plt.legend(loc='upper right')

            # Add a title
            plt.title(f'E-AODV Network Topology with Capabilities - Node: {self.node_id}')
            plt.axis('off')  # Turn off axis

            # Create a temporary file
            fd, path = tempfile.mkstemp(suffix='.png')
            os.close(fd)

            # Save the figure
            plt.savefig(path, format='png', dpi=150, bbox_inches='tight')
            plt.close()

            print(f"Visualization saved to: {path}")
            print("Attempting to open the image...")

            # Try to open the image with the default viewer
            try:
                if sys.platform == 'darwin':  # macOS
                    os.system(f"open {path}")
                elif sys.platform == 'win32':  # Windows
                    os.startfile(path)
                else:  # Linux
                    os.system(f"xdg-open {path}")
                print("Image opened in default viewer.")
            except Exception as e:
                print(f"Could not open image automatically: {e}")
                print(f"Please open the file manually: {path}")

            # Also provide HTML output option for more interactivity
            html_option = input("\nGenerate interactive HTML visualization? (y/n): ")
            if html_option.lower() == 'y':
                try:
                    import plotly.graph_objects as go
                    import plotly.offline as pyoff

                    # Create HTML visualization
                    html_fd, html_path = tempfile.mkstemp(suffix='.html')
                    os.close(html_fd)

                    # Debug output to confirm we're using the correct number of nodes
                    print(f"Creating visualization with {len(G.nodes())} nodes and {len(G.edges())} edges")

                    # Create edge traces
                    edge_x = []
                    edge_y = []
                    for source, target in G.edges():
                        x0, y0 = pos[source]
                        x1, y1 = pos[target]
                        edge_x.extend([x0, x1, None])
                        edge_y.extend([y0, y1, None])

                    edge_trace = go.Scatter(
                        x=edge_x, y=edge_y,
                        line=dict(width=2, color='#888'),
                        hoverinfo='none',
                        mode='lines')

                    # Create different node groups by capabilities
                    local_node_data = {"x": [], "y": [], "text": [], "hovertext": []}
                    sensor_node_data = {"x": [], "y": [], "text": [], "hovertext": []}
                    writable_node_data = {"x": [], "y": [], "text": [], "hovertext": []}
                    unknown_node_data = {"x": [], "y": [], "text": [], "hovertext": []}

                    # Populate node data groups
                    for node in G.nodes():
                        x, y = pos[node]
                        is_local = G.nodes[node].get("is_local", False)
                        mac = G.nodes[node].get("mac", "Unknown")
                        capabilities = G.nodes[node].get("capabilities", {})
                        sensors = G.nodes[node].get("sensors", [])
                        writable = G.nodes[node].get("writable", [])

                        # Basic node text
                        node_text = node

                        # Detailed hover text with capability info
                        hover_text = f"<b>ID:</b> {node}<br><b>MAC:</b> {mac}"

                        # Add capability details to hover text
                        if sensors or writable:
                            hover_text += "<br><br><b>Capabilities:</b>"
                            if sensors:
                                hover_text += f"<br>- Sensors: {', '.join(sensors)}"
                            if writable:
                                hover_text += f"<br>- Writable: {', '.join(writable)}"

                            # Add any sensor values if available
                            for cap, value in capabilities.items():
                                if cap.endswith("_value"):
                                    sensor_name = cap.replace("_value", "")
                                    hover_text += f"<br>- {sensor_name}: {value}"
                        else:
                            hover_text += "<br><br>No known capabilities"

                        # Add to appropriate group based on node type
                        if is_local:
                            local_node_data["x"].append(x)
                            local_node_data["y"].append(y)
                            local_node_data["text"].append(node_text)
                            local_node_data["hovertext"].append(hover_text)
                        elif writable:
                            writable_node_data["x"].append(x)
                            writable_node_data["y"].append(y)
                            writable_node_data["text"].append(node_text)
                            writable_node_data["hovertext"].append(hover_text)
                        elif sensors:
                            sensor_node_data["x"].append(x)
                            sensor_node_data["y"].append(y)
                            sensor_node_data["text"].append(node_text)
                            sensor_node_data["hovertext"].append(hover_text)
                        else:
                            unknown_node_data["x"].append(x)
                            unknown_node_data["y"].append(y)
                            unknown_node_data["text"].append(node_text)
                            unknown_node_data["hovertext"].append(hover_text)

                    # Create trace for the local node (red)
                    local_node_trace = go.Scatter(
                        x=local_node_data["x"], y=local_node_data["y"],
                        mode='markers+text',
                        name='Local Node',
                        marker=dict(
                            color='red',
                            size=20,
                            line=dict(width=2, color='darkred')
                        ),
                        text=local_node_data["text"],
                        hovertext=local_node_data["hovertext"],
                        hoverinfo='text',
                        textposition="top center"
                    )

                    # Create trace for sensor nodes (blue)
                    sensor_node_trace = go.Scatter(
                        x=sensor_node_data["x"], y=sensor_node_data["y"],
                        mode='markers+text',
                        name='Sensor Nodes',
                        marker=dict(
                            color='skyblue',
                            size=15,
                            line=dict(width=1, color='darkblue')
                        ),
                        text=sensor_node_data["text"],
                        hovertext=sensor_node_data["hovertext"],
                        hoverinfo='text',
                        textposition="top center"
                    )

                    # Create trace for writable nodes (green)
                    writable_node_trace = go.Scatter(
                        x=writable_node_data["x"], y=writable_node_data["y"],
                        mode='markers+text',
                        name='Writable Nodes',
                        marker=dict(
                            color='green',
                            size=15,
                            symbol='square',
                            line=dict(width=1, color='darkgreen')
                        ),
                        text=writable_node_data["text"],
                        hovertext=writable_node_data["hovertext"],
                        hoverinfo='text',
                        textposition="top center"
                    )

                    # Create trace for unknown capability nodes (gray)
                    unknown_node_trace = go.Scatter(
                        x=unknown_node_data["x"], y=unknown_node_data["y"],
                        mode='markers+text',
                        name='Unknown Capabilities',
                        marker=dict(
                            color='lightgray',
                            size=12,
                            line=dict(width=1, color='gray')
                        ),
                        text=unknown_node_data["text"],
                        hovertext=unknown_node_data["hovertext"],
                        hoverinfo='text',
                        textposition="top center"
                    )

                    # Build list of traces - include only non-empty ones
                    data_traces = [edge_trace]
                    if unknown_node_data["x"]:
                        data_traces.append(unknown_node_trace)
                    if sensor_node_data["x"]:
                        data_traces.append(sensor_node_trace)
                    if writable_node_data["x"]:
                        data_traces.append(writable_node_trace)
                    if local_node_data["x"]:
                        data_traces.append(local_node_trace)

                    # Create figure with all traces
                    fig = go.Figure(
                        data=data_traces,
                        layout=go.Layout(
                            title=f'E-AODV Network Topology with Capabilities - Node: {self.node_id}',
                            showlegend=True,
                            legend=dict(x=0, y=1.1),
                            hovermode='closest',
                            margin=dict(b=20, l=5, r=5, t=40),
                            annotations=[
                                dict(
                                    text=f"Total: {len(G.nodes())} nodes, {len(G.edges())} connections",
                                    showarrow=False,
                                    xref="paper", yref="paper",
                                    x=0.01, y=-0.05
                                )
                            ],
                            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False)
                        )
                    )

                    # Write to HTML file with config for better interactivity
                    config = {
                        'scrollZoom': True,
                        'displayModeBar': True,
                        'editable': True
                    }
                    pyoff.plot(fig, filename=html_path, auto_open=False, config=config)

                    # Try to open the HTML file in a browser
                    webbrowser.open('file://' + os.path.abspath(html_path))

                    print(f"Interactive visualization saved to: {html_path}")
                    print(
                        f"Displaying {len(G.nodes())} nodes and {len(G.edges())} connections with capability information")

                except ImportError:
                    print("Could not generate interactive visualization.")
                    print("Required libraries: plotly (pip install plotly)")
                except Exception as e:
                    print(f"Error generating interactive visualization: {str(e)}")
                    import traceback
                    print(traceback.format_exc())

        except Exception as e:
            print(f"Error generating visualization: {str(e)}")
            import traceback
            print(traceback.format_exc())

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="E-AODV Demo with Terminal Interface")
    parser.add_argument("--node-id", type=str, required=True, help="Node identifier")
    
    # Add capability flags
    parser.add_argument("--temp", action="store_true", help="Temperature sensor capability (default: enabled)")
    parser.add_argument("--no-temp", action="store_true", help="Disable temperature sensor capability")
    parser.add_argument("--humidity", action="store_true", help="Humidity sensor capability")
    parser.add_argument("--motion", action="store_true", help="Motion sensor capability")
    parser.add_argument("--motor", action="store_true", help="Motor capability")
    parser.add_argument("--display", action="store_true", help="Display capability")
    parser.add_argument("--camera", action="store_true", help="Camera capability")
    parser.add_argument("--led", action="store_true", help="LED capability")
    
    args = parser.parse_args()
    
    # Set capabilities based on arguments
    capabilities = {
        "temperature": not args.no_temp,  # Default enabled unless --no-temp is specified
        "humidity": args.humidity,
        "motion": args.motion,
        "motor": args.motor,
        "display": args.display,
        "camera": args.camera,
        "led": args.led
    }
    
    # Create and run demo
    demo = EAODVDemo(node_id=args.node_id, capabilities=capabilities)
    
    try:
        demo.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
        demo.quit()