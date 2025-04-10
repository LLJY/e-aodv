# EAODV: Topology Aware AODV Routing over Bluetooth

## Overview

This project implements an Enhanced Ad-Hoc On-Demand Distance Vector (EAODV) routing protocol designed specifically for Bluetooth Classic (RFCOMM) networks. It enables multi-hop communication between devices in a mesh network, allowing nodes that are not directly connected via Bluetooth to exchange data.

Key features include:
* **Reactive Route Discovery:** Establishes routes on-demand using E-RREQ and E-RREP packets.
* **Route Maintenance:** Detects and repairs broken links using E-RERR messages.
* **Topology & Capability Sharing:** Nodes share neighbor information, routing knowledge, and sensor/actuator capabilities via periodic R-HELLO messages.
* **Data Query & Write Operations:** Supports querying data (e.g., sensor readings) and writing data (e.g., controlling actuators) on remote nodes using specialized E-RREQ operations.
* **Network Configuration:** Allows network-wide parameters (like HELLO interval, TTL) to be configured and propagated.
* **Sensor Integration:** Includes a sensor registry and example sensor implementations (CPU temperature, simulated LED, motor, and physical sensors like DHT11, servo, etc.) allowing nodes to share real-world data.
* **Interactive Demo:** Provides a terminal-based user interface (`m.py`) for testing protocol features.
* **Network Visualization:** Option to generate static and interactive network diagrams using matplotlib/networkx and plotly.

## Prerequisites

* **Hardware:** Linux-based system with Bluetooth Classic support (e.g., Raspberry Pi). For physical sensor functionality, the corresponding hardware (DHT11, float switch, touch sensor, servo, LEDs, soil moisture sensor via Arduino) connected to the correct GPIO pins/serial ports is required.
* **Software:**
    * Python 3
    * `bluez` Bluetooth stack and associated command-line tools (`hciconfig`, `bluetoothctl`, `sdptool`)
    * Git
    * `sudo` access is required for running the main application and some setup steps.
    * Additional Python libraries for physical sensors (`RPi.GPIO`, `pyserial`, `adafruit-circuitpython-dht`).

## Setup Instructions

**Important:** Many Bluetooth operations require elevated privileges. It's recommended to run the setup and the main application using `sudo` or within a `sudo su` environment.

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/LLJY/e-aodv
    cd e-aodv
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
    * **Other Dependencies:** `requirements.txt` lists `pyserial` and `adafruit-circuitpython-dht`, potentially needed for physical sensors. Install these and `RPi.GPIO` (required for most physical sensors) after activating the virtual environment if you plan to use physical sensors:
        ```bash
        pip install RPi.GPIO pyserial adafruit-circuitpython-dht
        # You can also run: pip install -r requirements.txt (this won't install RPi.GPIO)
        ```

3.  **Pair Bluetooth Devices:**
    * Manually pair the Bluetooth devices you intend to use in the mesh network using `bluetoothctl`. Nodes must be paired *before* they can connect using the EAODV protocol.
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
    * The protocol uses RFCOMM channel 3. Conflicts with existing Serial Port Profile (SPP) records can cause connection issues. The `clear_sdp.sh` script helps clean up conflicting records and ensures the correct one is present. Run it with `sudo` if you encounter connection problems:
        ```bash
        chmod +x clear_sdp.sh
        sudo ./clear_sdp.sh
        ```

## Enabling Physical Sensors (Optional)

The project includes support for various physical sensors defined in `sensors/phy_sensors.py`. By default, these all are enabled, along with the default cpu temperature sensor. To enable physical sensors:

1.  **Configure Pins/Ports:**
    * Edit the file `sensors/phy_sensors.py`.
    * Locate the `initialize_sensors` function (it might be commented out). Inside this function, find the sensor initializations (e.g., `DHT11Sensor(pin=2, ...)`).
    * **Crucially, change the `pin=` or `port=` values in the constructors to match the actual GPIO pins or serial port (`/dev/ttyUSB0`, etc.) where your sensors are connected on your hardware.**
        * DHT11Sensor: `pin=` (e.g., GPIO2)
        * WaterLevelSensor: `pin=` (e.g., GPIO26)
        * TouchSensor: `pin=` (e.g., GPIO3)
        * ServoSensor: `pin=` (e.g., GPIO18)
        * ColorLEDSensor: `pin=` (e.g., GPIO20 for Red, GPIO16 for Green, GPIO21 for Yellow)
        * SoilMoistureSensor: `port=` (e.g., `/dev/ttyUSB0`)
    
**If your hardware does not support any of the sensors, you can choose to leave them in (simulate) or comment out the sensors in the `initialize_sensors` function located in `sensors/phy_sensors.py`**

2.  **Enable Initialization in Demo:**
    * Edit the main demo file `m.py`.
    * Find the section where the `EAODVDemo` class is instantiated (near the end of the file).
    * *After* the line `self.sensor_registry = SensorRegistry()` and *before* the line `self.eaodv = EAODVProtocol(...)`, add the following lines (uncommenting the import if necessary):
        ```python
        # Import the function if not already done near the top
        from sensors.phy_sensors import initialize_sensors

        # Initialize physical sensors, passing the registry and disabling simulation
        initialize_sensors(self.sensor_registry, simulate=False)
        ```
    * Make sure `simulate=False` is used if you have real hardware connected. If you want to test without hardware, you can leave it as `simulate=True` (or omit it if the default is True in the function).

3.  **Install Dependencies:** Ensure you have installed the necessary libraries as mentioned in Setup Step 2 (`RPi.GPIO`, `pyserial`, `adafruit-circuitpython-dht`). If you do not have the libraries, it will default to simulate mode.

## Running the Demo (`m.py`)

The main application (`m.py`) provides an interactive terminal interface to demonstrate the EAODV protocol features.

**Important:** Due to the need for direct Bluetooth hardware access, RFCOMM port binding, and potentially GPIO access for sensors, you **must** run the demo script with `sudo`.

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
    * `[capability-flags]`: Optional flags to enable specific capabilities for this node (e.g., `--led`, `--motor`, `--humidity`). Note that physical sensors enabled in the previous step will register their capabilities automatically. By default, the CPU temperature sensor is enabled unless `--no-temp` is used.

    **Example:**
    ```bash
    sudo python m.py --node-id raspi-A --led --motor
    ```

3.  **Using the Demo Interface:**
    Once running, `m.py` presents a menu with options to:
    * Discover nearby EAODV nodes.
    * Connect to discovered nodes.
    * Show currently connected nodes and their capabilities (including physical sensors if enabled).
    * Find routes to specific nodes (by MAC address).
    * Show the node's routing table.
    * Show the perceived network topology.
    * Query data (capabilities, sensor readings, config) from remote nodes.
    * Write data (control actuators like LEDs, motors, servos) to remote nodes.
    * Configure network-wide parameters (HELLO interval, TTL, etc.).
    * Generate network visualization diagrams (requires `matplotlib`/`networkx`/`plotly`).

## Important Notes

* **Root Privileges:** Running the main application (`m.py`) and scripts like `clear_sdp.sh` and `setup_venv.sh` **requires `sudo`** due to interactions with the Bluetooth stack, network configuration, GPIO access, and package installation.
* **Bluetooth Stack:** The system relies heavily on the Linux `bluez` stack and command-line tools. Ensure `bluez` is installed and running correctly.
* **Pairing:** Devices *must* be paired using system Bluetooth tools (`bluetoothctl`) before the EAODV protocol can establish connections.
* **RFCOMM Channel:** The application uses RFCOMM channel 3. Ensure this channel is not heavily used by other services.
* **Sensors:** Physical sensor interaction depends on correct hardware connections, GPIO/port configuration in `sensors/phy_sensors.py`, and necessary libraries (`RPi.GPIO`, `adafruit-circuitpython-dht`, `pyserial`) being installed. Ensure you have the correct permissions to access GPIO and serial ports.
