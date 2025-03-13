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
from sensors.sensor_registry import SensorRegistry
from sensors.sensor import Sensor
from sensors.cpu_temperature import CPUTemperatureSensor

# Import EAODV protocol
from eaodv_protocol import EAODVProtocol, OperationType

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
        print("11. Quit")
    
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
        Query data from a remote node.
        """
        self._clear_screen()
        print("\n=== Query Node Data ===")
        
        # Get destination node
        dest_mac = input("\nEnter destination MAC address: ")
        
        if not dest_mac:
            print("MAC address is required.")
            input("\nPress Enter to continue...")
            return
        
        # Show query options
        print("\nQuery types:")
        print("1. Temperature")
        print("2. Humidity")
        print("3. Node capabilities")
        print("4. Network configuration")
        print("5. Neighbor list")
        
        query_choice = input("\nSelect query type (1-5): ")
        
        query_type = None
        if query_choice == "1":
            query_type = "temperature"
        elif query_choice == "2":
            query_type = "humidity"
        elif query_choice == "3":
            query_type = "capabilities"
        elif query_choice == "4":
            query_type = "network_config"
        elif query_choice == "5":
            query_type = "neighbors"
        else:
            print("Invalid query type.")
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
        
        if query_result["success"]:
            print("\nQuery successful!")
            print("Response data:")
            
            # Format response for different query types
            data = query_result["data"]
            if query_type == "temperature":
                if "temperature" in data:
                    print(f"Temperature: {data['temperature']}°C")
                else:
                    print("Temperature data not available")
            elif query_type == "humidity":
                if "humidity" in data:
                    print(f"Humidity: {data['humidity']}%")
                else:
                    print("Humidity data not available")
            elif query_type == "capabilities":
                if "capabilities" in data:
                    caps = data["capabilities"]
                    for cap, enabled in caps.items():
                        if cap != "temperature_value":
                            status = "Enabled" if enabled else "Disabled"
                            print(f"- {cap}: {status}")
                    if "temperature_value" in caps:
                        print(f"- Temperature: {caps['temperature_value']}°C")
                else:
                    print("Capabilities data not available")
            elif query_type == "network_config":
                if "hello_interval" in data:
                    print(f"Hello Interval: {data['hello_interval']} seconds")
                if "include_sensor_data" in data:
                    print(f"Include Sensor Data: {'Yes' if data['include_sensor_data'] else 'No'}")
            elif query_type == "neighbors":
                if "neighbors" in data:
                    neighbors = data["neighbors"]
                    if neighbors:
                        for i, neighbor in enumerate(neighbors):
                            node_id = neighbor.get("node_id", "Unknown")
                            mac = neighbor.get("bt_mac_address", "Unknown")
                            print(f"{i+1}. {node_id} ({mac})")
                    else:
                        print("No neighbors reported")
                else:
                    print("Neighbor data not available")
            else:
                print(json.dumps(data, indent=2))
        else:
            print("Query failed.")
            if "message" in query_result.get("data", {}):
                print(f"Error: {query_result['data']['message']}")
            
        input("\nPress Enter to continue...")
    
    def write_to_node(self):
        """
        Write data to a remote node.
        """
        self._clear_screen()
        print("\n=== Write to Node ===")
        
        # Get destination node
        dest_mac = input("\nEnter destination MAC address: ")
        
        if not dest_mac:
            print("MAC address is required.")
            input("\nPress Enter to continue...")
            return
        
        # Show write options
        print("\nWrite options:")
        print("1. Set LED state")
        print("2. Set motor speed")
        print("3. Set display message")
        
        write_choice = input("\nSelect write option (1-3): ")
        
        write_data = {}
        if write_choice == "1":
            led_state = input("Enter LED state (on/off): ").lower()
            if led_state not in ["on", "off"]:
                print("Invalid LED state. Must be 'on' or 'off'.")
                input("\nPress Enter to continue...")
                return
            write_data["led"] = (led_state == "on")
        elif write_choice == "2":
            try:
                motor_speed = int(input("Enter motor speed (0-100): "))
                if motor_speed < 0 or motor_speed > 100:
                    raise ValueError("Motor speed must be between 0 and 100")
                write_data["motor"] = motor_speed
            except ValueError as e:
                print(f"Invalid motor speed: {e}")
                input("\nPress Enter to continue...")
                return
        elif write_choice == "3":
            display_message = input("Enter display message: ")
            write_data["display"] = display_message
        else:
            print("Invalid write option.")
            input("\nPress Enter to continue...")
            return
        
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