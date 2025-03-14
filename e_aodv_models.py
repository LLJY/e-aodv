from datetime import datetime
import json
import uuid
import copy
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any
from enum import Enum


#
# === ENUMS & CONSTANTS ===
#
class RequestType(Enum):
    E_RREQ = 1
    E_RREP = 2
    E_RERR = 3
    R_HELLO = 4
    R_TOP = 5


class OperationType(Enum):
    ROUTE = 1  # Standard route discovery
    QUERY = 2  # Query data from a node
    WRITE = 3  # Write data to a node
    CONFIG = 4  # Configure network parameters


#
# === DATA STRUCTURES ===
#

@dataclass
class TopologyEntry:
    """
    A single node entry in the packet topology.
    node_id can be None if the node only identifies with MAC.
    """
    node_id: Optional[str]
    bt_mac_address: str
    neighbors: List[str]


@dataclass
class E_RREQ:
    """
    Enhanced Route Request packet for EAODV.
    """
    type: int = RequestType.E_RREQ.value
    source_id: str = ""
    source_mac: str = ""
    destination_id: Optional[str] = None
    destination_mac: Optional[str] = None
    broadcast_id: str = ""
    sequence_number: int = 0
    hop_count: int = 0
    time_to_live: int = 0
    timestamp: str = ""
    previous_hop_mac: str = ""
    packet_topology: List[TopologyEntry] = field(default_factory=list)
    # New fields for enhanced functionality
    operation_type: int = OperationType.ROUTE.value  # Default to route discovery
    query_params: Dict[str, Any] = field(default_factory=dict)  # Parameters for query/write operations

    def __init__(
            self,
            src_id: str,
            src_mac: str,
            dest_id: str,
            sequence_number: int,
            time_to_live: int,
            dest_mac: Optional[str] = None,
            operation_type: int = OperationType.ROUTE.value,
            query_params: Optional[Dict[str, Any]] = None
    ):
        self.type = RequestType.E_RREQ.value
        self.source_id = src_id
        self.source_mac = src_mac
        self.destination_id = dest_id
        self.destination_mac = dest_mac
        self.broadcast_id = uuid.uuid4().hex
        self.sequence_number = sequence_number
        self.time_to_live = time_to_live
        self.hop_count = 0
        self.timestamp = str(datetime.now())
        self.previous_hop_mac = src_mac
        self.packet_topology = []
        self.operation_type = operation_type
        self.query_params = query_params or {}

    def send_prepare(self) -> None:
        """
        Called right before sending the initial E-RREQ from the source.
        Sets the timestamp and resets the packet topology if needed.
        """
        self.timestamp = str(datetime.now())
        self.packet_topology.clear()
        self.previous_hop_mac = self.source_mac

    def forward_prepare(self, current_node_mac: str, current_node_id: Optional[str], neighbors: List[str],
                        routing_knowledge: Optional[List[Dict[str, Any]]] = None) -> None:
        """
        Prepares the packet for forwarding through an intermediate node.
        Enhances topology information with node's routing knowledge.

        Args:
            current_node_mac: MAC address of the current node
            current_node_id: ID of the current node (optional)
            neighbors: List of neighbor MAC addresses
            routing_knowledge: Optional routing knowledge to enhance topology information
        """
        self.time_to_live -= 1
        self.hop_count += 1
        self.timestamp = str(datetime.now())
        self.previous_hop_mac = current_node_mac

        # Append the current node's info to the topology
        new_entry = TopologyEntry(
            node_id=current_node_id,
            bt_mac_address=current_node_mac,
            neighbors=neighbors
        )
        self.packet_topology.append(new_entry)

        # Enhance with additional routing knowledge if provided
        # This is limited to avoid excessive packet size
        if routing_knowledge and len(routing_knowledge) <= 5:  # Limit to 5 routes
            for route in routing_knowledge:
                dest_mac = route.get("destination", {}).get("bt_mac_address")
                if dest_mac and dest_mac != self.destination_mac and len(route.get("hops", [])) <= 2:
                    # Only include short paths to other destinations
                    for hop in route.get("hops", []):
                        if hop.get("bt_mac_address") not in [entry.bt_mac_address for entry in self.packet_topology]:
                            # Add this hop's information to enrich topology
                            self.packet_topology.append(TopologyEntry(
                                node_id=hop.get("node_id"),
                                bt_mac_address=hop.get("bt_mac_address", ""),
                                neighbors=hop.get("neighbors", [])
                            ))

    def is_final_destination(self, current_node_mac: str) -> bool:
        """
        Checks if the current node is the intended final destination.
        """

        # If the E-RREQ is routed by MAC:
        if self.destination_mac and self.destination_mac == current_node_mac:
            return True
        # If the E-RREQ is routed by ID (optional fallback):
        if self.destination_id and self.destination_id.lower() == current_node_mac.lower():
            return True
        return False


@dataclass
class E_RREP:
    """
    Enhanced Route Reply packet for EAODV.
    Typically constructed by the destination or an intermediate node
    with a valid route, and unicast back to the source.
    """
    type: int = field(default=RequestType.E_RREP.value)
    source_id: str = ""
    source_mac: str = ""
    destination_id: str = ""
    destination_mac: str = ""
    broadcast_id: str = ""
    sequence_number: int = 0
    hop_count: int = 0
    timestamp: str = ""
    packet_topology: List[TopologyEntry] = field(default_factory=list)
    # New fields for enhanced functionality
    operation_type: int = OperationType.ROUTE.value  # Default to route discovery
    response_data: Dict[str, Any] = field(default_factory=dict)  # Response data for queries

    @classmethod
    def from_erreq(cls, erreq: "E_RREQ") -> "E_RREP":
        """
        Factory method that creates an E_RREP from an E_RREQ packet.
        Copies relevant fields and inverts source/destination as needed.
        """
        return cls(
            source_id=erreq.destination_id or "",
            source_mac=erreq.destination_mac or "",
            destination_id=erreq.source_id,
            destination_mac=erreq.source_mac,
            broadcast_id=uuid.uuid4().hex,
            sequence_number=erreq.sequence_number,
            hop_count=erreq.hop_count,
            timestamp=str(datetime.now()),
            packet_topology=copy.deepcopy(erreq.packet_topology),  # deep copy of the list
            operation_type=erreq.operation_type,  # Copy operation type
            response_data={}  # Initialize empty response data
        )

    def prepare_reply(self, node_mac: str, node_id: Optional[str], neighbors: List[str]) -> None:
        """
        Called by intermediate nodes (or the final node) when building
        or updating the E_RREP on its way back to the source.
        Increments the hop count, updates the timestamp, and appends new topology info.
        """
        self.hop_count += 1
        self.timestamp = str(datetime.now())

        new_entry = TopologyEntry(
            node_id=node_id,
            bt_mac_address=node_mac,
            neighbors=neighbors
        )
        self.packet_topology.append(new_entry)


@dataclass
class E_RERR:
    """
    Enhanced Route Error packet for EAODV.
    Broadcast when a node or link fails so that routes can be invalidated.
    """
    type: int = field(default=RequestType.E_RERR.value)
    failed_node_id: str = ""
    failed_node_mac: str = ""
    originator_id: str = ""
    originator_mac: str = ""
    timestamp: str = field(default_factory=lambda: str(datetime.now()))
    reason: Optional[str] = None

    def prepare_error(
            self,
            failed_node_id: str,
            failed_node_mac: str,
            originator_id: str,
            originator_mac: str,
            reason: Optional[str] = None
    ) -> None:
        """
        Fill in or update the fields of the E_RERR.
        """
        self.failed_node_id = failed_node_id
        self.failed_node_mac = failed_node_mac
        self.originator_id = originator_id
        self.originator_mac = originator_mac
        self.reason = reason
        self.timestamp = str(datetime.now())


@dataclass
class R_HELLO:
    """
    Hello message broadcast when a node joins the network or periodically,
    allowing neighbors to register or refresh their routes and share topology knowledge.
    """
    type: int = field(default=RequestType.R_HELLO.value)
    node_id: str = ""
    bt_mac_address: str = ""
    timestamp: str = field(default_factory=lambda: str(datetime.now()))
    initial_neighbors: List[str] = field(default_factory=list)
    # Routing knowledge - simplified version of routing table entries
    routing_knowledge: List[Dict[str, Any]] = field(default_factory=list)
    # Add flag to indicate if sensor data should be included
    include_sensor_data: bool = False
    # Add configurable hello interval (in seconds)
    hello_interval: int = 30


#
# === NETWORK CONFIGURATION ===
#
@dataclass
class NetworkConfig:
    """
    Network-wide configuration parameters that can be propagated.
    """
    hello_interval: int = 30  # Default hello interval in seconds
    include_sensor_data: bool = False  # Whether hello messages include sensor data by default
    ttl_default: int = 10  # Default TTL for RREQ messages
    route_cache_timeout: int = 300  # Route cache timeout in seconds


#
# === ROUTING STATE & TABLE ===
#
@dataclass
class RoutingTableEntry:
    """
    Represents one route entry for a destination.
    'host' is the destination's ID or MAC.
    'hops' is a list of intermediate nodes leading to that destination.
    """
    host: TopologyEntry
    hops: List[TopologyEntry]


@dataclass
class AodvState:
    """
    Maintains the local node's EAODV state, including
    its MAC address, sequence number, neighbor list,
    and a routing table containing discovered routes.
    """
    mac_address: str
    sequence_number: int
    neighbours: List[TopologyEntry]
    routing_table: List[RoutingTableEntry]

    def increment_sequence_number(self) -> None:
        """
        Increment the node's sequence number whenever a new route discovery
        or major network event occurs (standard AODV behavior).
        """
        self.sequence_number += 1

    def add_neighbor(self, neighbor_mac: str, neighbor_id: Optional[str], neighbors: List[str]) -> None:
        """
        Add or update a neighbor in the local neighbor list.
        """
        entry = next((n for n in self.neighbours if n.bt_mac_address == neighbor_mac), None)
        if entry:
            # Update existing neighbor
            entry.node_id = neighbor_id
            entry.neighbors = neighbors
        else:
            # Create new neighbor entry
            self.neighbours.append(TopologyEntry(node_id=neighbor_id, bt_mac_address=neighbor_mac, neighbors=neighbors))

    def add_or_update_route(self, dest_mac: str, dest_id: Optional[str], hops: List[TopologyEntry]) -> None:
        """
        Add or update an entry in the routing table for a given destination.
        """
        existing_route = next(
            (r for r in self.routing_table if r.host.bt_mac_address == dest_mac),
            None
        )
        if existing_route:
            existing_route.host.node_id = dest_id
            existing_route.hops = hops
        else:
            new_host = TopologyEntry(node_id=dest_id, bt_mac_address=dest_mac, neighbors=[])
            self.routing_table.append(RoutingTableEntry(host=new_host, hops=hops))

    def remove_route(self, dest_mac: str) -> None:
        """
        Remove a route from the routing table if it exists.
        """
        self.routing_table = [
            route for route in self.routing_table
            if route.host.bt_mac_address != dest_mac
        ]


#
# === SERIALIZATION HELPERS ===
#
def to_json(obj) -> str:
    """Serialize a dataclass object to a JSON string."""
    return json.dumps(asdict(obj), indent=2)


def from_json(json_str: str, cls):
    """Deserialize a JSON string into an instance of the given class."""
    data = json.loads(json_str)

    # Handle packet_topology entries for E_RREQ and E_RREP
    if cls in (E_RREQ, E_RREP) and "packet_topology" in data:
        topology_data = data.get("packet_topology", [])
        data["packet_topology"] = [TopologyEntry(**entry) for entry in topology_data]

    # Create instance with deserialized data
    return cls(**data)


#
# === EXAMPLE USAGE ===
#
if __name__ == "__main__":
    # Create a sample E-RREQ message for route discovery
    erreq = E_RREQ(
        src_id="node-A",
        src_mac="AA:BB:CC:DD:EE:01",
        dest_id="node-B",
        sequence_number=10,
        time_to_live=10,
        dest_mac="AA:BB:CC:DD:EE:99"
    )
    erreq.send_prepare()

    # Show serialized E-RREQ
    json_str = to_json(erreq)
    print("[E-RREQ] Route Discovery Serialized JSON:\n", json_str)

    # Create a sample E-RREQ message for querying temperature
    query_req = E_RREQ(
        src_id="node-A",
        src_mac="AA:BB:CC:DD:EE:01",
        dest_id="node-B",
        sequence_number=11,
        time_to_live=10,
        dest_mac="AA:BB:CC:DD:EE:99",
        operation_type=OperationType.QUERY.value,
        query_params={"query": "temperature"}
    )
    query_req.send_prepare()

    # Show serialized query E-RREQ
    json_str = to_json(query_req)
    print("\n[E-RREQ] Query Serialized JSON:\n", json_str)

    # Create a sample E-RREQ message for network configuration
    config_req = E_RREQ(
        src_id="node-A",
        src_mac="AA:BB:CC:DD:EE:01",
        dest_id="node-B",
        sequence_number=12,
        time_to_live=10,
        dest_mac="AA:BB:CC:DD:EE:99",
        operation_type=OperationType.CONFIG.value,
        query_params={"hello_interval": 45, "include_sensor_data": True}
    )
    config_req.send_prepare()

    # Show serialized config E-RREQ
    json_str = to_json(config_req)
    print("\n[E-RREQ] Config Serialized JSON:\n", json_str)

    # Create an E-RREP from the query E-RREQ (for demonstration)
    e_rrep = E_RREP.from_erreq(query_req)
    e_rrep.prepare_reply(
        node_mac="AA:BB:CC:DD:EE:02",
        node_id="node-C",
        neighbors=["node-D", "node-E"]
    )
    # Add response data for the query
    e_rrep.response_data = {"temperature": 24.5, "status": "ok"}

    rep_json = to_json(e_rrep)
    print("\n[E-RREP] Query Response Serialized JSON:\n", rep_json)

    # Demonstrate E_RERR
    e_rerr = E_RERR()
    e_rerr.prepare_error(
        failed_node_id="node-F",
        failed_node_mac="AA:BB:CC:DD:EE:0F",
        originator_id="node-A",
        originator_mac="AA:BB:CC:DD:EE:01",
        reason="Link failure"
    )
    print("\n[E-RERR] Serialized JSON:\n", to_json(e_rerr))

    # Demonstrate R_HELLO with sensor data configuration
    hello = R_HELLO()
    hello.node_id = "node-A"
    hello.bt_mac_address = "AA:BB:CC:DD:EE:01"
    hello.initial_neighbors = ["AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"]
    hello.include_sensor_data = True
    hello.hello_interval = 45

    hello_json = to_json(hello)
    print("\n[R_HELLO] Serialized JSON with sensor data flag:\n", hello_json)

    # Demonstrate AodvState usage
    my_state = AodvState(
        mac_address="AA:BB:CC:DD:EE:01",
        sequence_number=1,
        neighbours=[],
        routing_table=[]
    )
    my_state.add_neighbor(neighbor_mac="AA:BB:CC:DD:EE:02", neighbor_id="node-C", neighbors=["node-D"])
    my_state.add_or_update_route(
        dest_mac="AA:BB:CC:DD:EE:99",
        dest_id="node-B",
        hops=[TopologyEntry("node-C", "AA:BB:CC:DD:EE:02", ["node-D", "node-E"])]
    )
    print("\n[AodvState] Current State:\n", my_state)

    # Demonstrate NetworkConfig
    net_config = NetworkConfig(
        hello_interval=45,
        include_sensor_data=True,
        ttl_default=15,
        route_cache_timeout=600
    )
    print("\n[NetworkConfig] Configuration:\n", net_config)