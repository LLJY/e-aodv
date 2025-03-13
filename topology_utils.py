"""
Utility functions for E-AODV topology optimization and management.
"""
import logging
import time
from typing import Dict, List, Any, Set, Optional

logger = logging.getLogger(__name__)


def optimize_topology(topology_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optimize topology data by removing redundancies and consolidating information.

    Args:
        topology_entries: List of topology entries to optimize

    Returns:
        Optimized list of topology entries
    """
    if not topology_entries:
        return []

    # Track seen nodes to avoid duplicates
    seen_nodes: Set[str] = set()
    optimized_entries = []

    # First pass: collect all unique nodes and their most recent information
    node_info: Dict[str, Dict[str, Any]] = {}

    for entry in topology_entries:
        # Skip invalid entries
        if not isinstance(entry, dict) or "bt_mac_address" not in entry:
            continue

        mac = entry.get("bt_mac_address")
        if not mac:
            continue

        # Update info for this node with most recent data
        if mac not in node_info or entry.get("timestamp", 0) > node_info[mac].get("timestamp", 0):
            node_info[mac] = entry.copy()

    # Second pass: consolidate neighbor information
    for mac, entry in node_info.items():
        neighbors = set(entry.get("neighbors", []))

        # Collect additional neighbors from other entries for this node
        for other_entry in topology_entries:
            if other_entry.get("bt_mac_address") == mac:
                neighbors.update(other_entry.get("neighbors", []))

        # Update consolidated neighbors
        entry["neighbors"] = list(neighbors)
        optimized_entries.append(entry)

    logger.debug(f"Optimized topology: {len(topology_entries)} entries -> {len(optimized_entries)} entries")
    return optimized_entries


def clean_topology_at_destination(packet_data: Dict[str, Any], local_mac: str) -> Dict[str, Any]:
    """
    Clean and optimize topology data in a packet at the destination.

    Args:
        packet_data: The packet data containing topology information
        local_mac: MAC address of the local node

    Returns:
        Updated packet data with optimized topology
    """
    # Handle packet_topology for RREQ/RREP
    if "packet_topology" in packet_data:
        # Optimize topology entries
        original_count = len(packet_data["packet_topology"])
        packet_data["packet_topology"] = optimize_topology(packet_data["packet_topology"])
        logger.debug(f"Cleaned packet topology: {original_count} -> {len(packet_data['packet_topology'])} entries")

    # Return the updated packet data
    return packet_data