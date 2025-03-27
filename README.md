# EAODV-Gemini: Enhanced AODV Routing over Bluetooth

## Overview

This project implements an Enhanced Ad-Hoc On-Demand Distance Vector (EAODV) routing protocol designed specifically for Bluetooth Classic (RFCOMM) networks[cite: 1]. It enables multi-hop communication between devices in a mesh network, allowing nodes that are not directly connected via Bluetooth to exchange data.

Key features include:
* **Reactive Route Discovery:** Establishes routes on-demand using E-RREQ and E-RREP packets.
* **Route Maintenance:** Detects and repairs broken links using E-RERR messages.
* **Topology & Capability Sharing:** Nodes share neighbor information, routing knowledge, and sensor/actuator capabilities via periodic R-HELLO messages.
* **Data Query & Write Operations:** Supports querying data (e.g., sensor readings) and writing data (e.g., controlling actuators) on remote nodes using specialized E-RREQ operations.
* **Network Configuration:** Allows network-wide parameters (like HELLO interval, TTL) to be configured and propagated.
* **Sensor Integration:** Includes a sensor registry and example sensor implementations (CPU temperature, simulated LED, motor) allowing nodes to share real-world data.
* **Interactive Demo:** Provides a terminal-based user interface (`m.py`) for testing protocol features.
* **Network Visualization:** Option to generate static and interactive network diagrams using matplotlib/networkx and plotly.

## Prerequisites

* **Hardware:** Linux-based system with Bluetooth Classic support (e.g., Raspberry Pi). Physical sensor hardware (DHT11, water level, touch, servo, LEDs, soil moisture sensor connected via Arduino) is required for full functionality, although some sensors have simulation modes.
* **Software:**
    * Python 3
    * `bluez` Bluetooth stack and associated command-line tools (`hciconfig`, `bluetoothctl`, `sdptool`) [cite: 1]
    * Git
    * `sudo` access is required for running the main application and some setup steps.

## Setup Instructions

**Important:** Many Bluetooth operations require elevated privileges. It's recommended to run the setup and the main application using `sudo` or within a `sudo su` environment.

1.  **Clone the Repository:**
    ```bash
    git clone <repository-url>
    cd aodv-gemini
    ```

2.  **Create Virtual Environment & Install Dependencies:**
    Use the provided script to set up a Python virtual environment. Run this script with `sudo` as it installs packages and might interact with system components.
    ```bash
    chmod +x setup_venv.sh venv_activate.sh
    sudo ./setup_venv.sh
    source venv/bin/activate
    # or use: ./venv_activate.sh
    ```
    * **Special Note for PyBluez:** This script handles the installation of dependencies. Crucially, `PyBluez` **must** be installed directly from its Git repository as shown in the `setup_venv.sh` script (`pip install git+https://github.com/pybluez/pybluez.git#egg=pybluez`). Standard installation via `pip install PyBluez` may fail or result in errors. The script also installs `matplotlib`, `networkx`, and `plotly`.
    * **Other Dependencies:** `requirements.txt` lists additional dependencies like `pyserial` and `adafruit-circuitpython-dht`. These might be needed if using the specific physical sensors defined in `sensors/phy_sensors.py`. If required, install them after activating the virtual environment: `pip install -r requirements.txt`.

3.  **Pair Bluetooth Devices:**
    * Manually pair the Bluetooth devices you intend to use in the mesh network using `bluetoothctl`. Nodes must be paired *before* they can connect using the EAODV protocol[cite: 1].
    * Example pairing process (run `bluetoothctl` possibly with `sudo`):
        ```bash
        sudo bluetoothctl
        # Inside bluetoothctl:
        scan on          # Discover devices
        # Note the MAC address (e.g., AA:BB:CC:DD:EE:FF) of the target device
        pair AA:BB:CC:DD:EE:FF
        trust AA:BB:CC:DD:EE:FF
        scan off
        quit
        ```
    * Repeat the pairing process on all devices that need to communicate.

4.  **SDP Record Check (Optional but Recommended):**
    * The protocol uses RFCOMM channel 3[cite: 1]. Conflicts with existing Serial Port Profile (SPP) records can cause connection issues. The `clear_sdp.sh` script helps clean up conflicting records and ensures the correct one is present. Run it with `sudo` if you encounter connection problems:
        ```bash
        chmod +x clear_sdp.sh
        sudo ./clear_sdp.sh
        ```

## Running the Demo (`m.py`)

The main application (`m.py`) provides an interactive terminal interface to demonstrate the EAODV protocol features.

**Important:** Due to the need for direct Bluetooth hardware access and RFCOMM port binding, you **must** run the demo script with `sudo`.

1.  **Activate Virtual Environment:**
    ```bash
    source venv/bin/activate
    # or use: ./venv_activate.sh
    ```

2.  **Run the Demo (with `sudo`):**
    Each node needs a unique ID. Specify the node ID and any capabilities it should advertise.
    ```bash
    sudo python m.py --node-id <your-unique-node-id> [capability-flags]
    ```
    * `<your-unique-node-id>`: A descriptive name for this node (e.g., `node1`, `raspi-A`). This is required.
    * `[capability-flags]`: Optional flags to enable specific capabilities for this node (e.g., `--led`, `--motor`, `--humidity`). By default, the CPU temperature sensor is enabled unless `--no-temp` is used.

    **Example:**
    ```bash
    sudo python m.py --node-id raspi-A --led --motor
    ```

3.  **Using the Demo Interface:**
    Once running, `m.py` presents a menu with options to:
    * Discover nearby EAODV nodes.
    * Connect to discovered nodes.
    * Show currently connected nodes and their capabilities.
    * Find routes to specific nodes (by MAC address).
    * Show the node's routing table.
    * Show the perceived network topology.
    * Query data (capabilities, sensor readings, config) from remote nodes.
    * Write data (control actuators like LEDs, motors) to remote nodes.
    * Configure network-wide parameters (HELLO interval, TTL, etc.).
    * Generate network visualization diagrams (requires `matplotlib`/`networkx`/`plotly`).

## Important Notes

* **Root Privileges:** Running the main application (`m.py`) and scripts like `clear_sdp.sh` and `setup_venv.sh` **requires `sudo`** due to interactions with the Bluetooth stack, network configuration, and package installation[cite: 1].
* **Bluetooth Stack:** The system relies heavily on the Linux `bluez` stack and command-line tools[cite: 1]. Ensure `bluez` is installed and running correctly.
* **Pairing:** Devices *must* be paired using system Bluetooth tools (`bluetoothctl`) before the EAODV protocol can establish connections[cite: 1].
* **RFCOMM Channel:** The application uses RFCOMM channel 3[cite: 1]. Ensure this channel is not heavily used by other services.
* **Sensors:** Physical sensor interaction depends on correct GPIO setup and libraries (`RPi.GPIO`, `adafruit-circuitpython-dht`, `pyserial`) being installed and configured. Simulation mode is available for testing without hardware.