#!/usr/bin/python3

import argparse
import asyncio
import errno
import fcntl
import os
import platform
import sys
import time
from pathlib import Path
from pprint import pprint
from struct import unpack_from
from bleak import BleakScanner

def _bleak_version():
    try:
        from importlib.metadata import version
        return version("bleak")
    except Exception:
        pass
    try:
        import bleak as _bleak
        return getattr(_bleak, "__version__", "unknown")
    except Exception:
        return "unknown"

BLEAK_VERSION = _bleak_version()

"""
usage: goveelog.py [-h] [-r] [-v]

optional arguments:
  -h, --help            show this help message and exit
  -r, --raw             print raw data to stddout
  --influxdb            publish to influxdb
  --influxdb_host INFLUXDB_HOST
                        hostname or ip of InfluxDb HTTP API
  --influxdb_port INFLUXDB_PORT
                        port of InfluxDb HTTP API
  --influxdb_user INFLUXDB_USER
                        InfluxDb username
  --influxdb_pass INFLUXDB_PASS
                        InfluxDb password
  --influxdb_db INFLUXDB_DB
                        InfluxDb database name
  -v, --verbose         verbose output to watch the threads
"""

govee_devices = {}
log_interval = 59
last_packet_timestamp = 0
GOVEE_MFG_IDS = (0xEC88, 0x88EC, 0x0188)

import subprocess
import time

# Lockfile path for single-instance enforcement
# Try /var/run first, but fall back to home directory if not writable
def get_lockfile_path():
    """Determine the best lockfile path, checking if /var/run is writable."""
    var_run_path = Path("/var/run/goveelog.pid")
    home_path = Path.home() / ".goveelog.pid"
    
    if os.path.exists("/var/run"):
        # Check if /var/run is writable
        try:
            # Try to create a test file
            test_file = Path("/var/run/.goveelog_test")
            try:
                test_file.touch()
                test_file.unlink()
                return var_run_path
            except (IOError, OSError):
                # Not writable, use home directory
                return home_path
        except:
            # If we can't check, default to home directory to be safe
            return home_path
    else:
        return home_path

LOCKFILE = get_lockfile_path()

# ###########################################################################

class InstanceLock:
    """Ensures only one instance of the script can run at a time."""
    def __init__(self, lockfile_path, force=False, verbose=False):
        self.lockfile_path = lockfile_path
        self.lockfile = None
        self.force = force
        self.verbose = verbose
        
    def _debug(self, msg):
        """Print debug message if verbose mode is enabled."""
        if self.verbose:
            print(f"[LOCK DEBUG] {msg}")
        
    def _is_pid_running(self, pid):
        """Check if a process with the given PID is actually running."""
        self._debug(f"Checking if PID {pid} is running...")
        try:
            # Try to send signal 0 to check if process exists
            # This doesn't kill the process, just checks if it's running
            os.kill(int(pid), 0)
            self._debug(f"PID {pid} is running")
            return True
        except (OSError, ValueError) as e:
            self._debug(f"PID {pid} is NOT running (error: {e})")
            return False
    
    def _check_and_clean_stale_lock(self):
        """Check if lockfile exists and if the PID is still running. Remove if stale.
        Returns: True if lockfile was cleaned or doesn't exist (can proceed),
                 None if lock is valid (process running, will try to acquire for proper error),
                 False if stale lockfile exists but force not set (cannot proceed)."""
        self._debug(f"Checking for lockfile at: {self.lockfile_path}")
        if not self.lockfile_path.exists():
            self._debug("No lockfile exists, can proceed")
            return True  # No lockfile, can proceed
            
        self._debug(f"Lockfile exists at: {self.lockfile_path}")
        try:
            with open(self.lockfile_path, 'r') as f:
                pid = f.read().strip()
            self._debug(f"Read PID from lockfile: {pid}")
            
            if pid and self._is_pid_running(pid):
                self._debug("Lock is valid, process is running - will try to acquire for proper error")
                return None  # Lock is valid, process is running - proceed to try acquiring for proper error
            else:
                # Stale lockfile - process is not running
                self._debug(f"Stale lockfile detected (PID {pid} not running, force={self.force})")
                if self.force or not pid:
                    print(f"Removing stale lockfile (PID {pid} not running)")
                    self.lockfile_path.unlink()
                    self._debug("Stale lockfile removed")
                    return True
                else:
                    print(f"ERROR: Stale lockfile found (PID {pid} not running)")
                    print(f"      Lockfile: {self.lockfile_path}")
                    print(f"      Use --force to remove it and continue")
                    self._debug("Stale lockfile exists but force not set, returning False")
                    return False
        except Exception as e:
            self._debug(f"Exception while checking lockfile: {e}")
            # If we can't read the lockfile, try to remove it if force is set
            if self.force:
                try:
                    self.lockfile_path.unlink()
                    self._debug("Removed lockfile due to exception (force mode)")
                    return True
                except Exception as e2:
                    self._debug(f"Failed to remove lockfile: {e2}")
                    pass
            self._debug("Returning False due to exception")
            return False
        
    def acquire(self):
        """Acquire the lockfile. Returns True if successful, False if already locked."""
        self._debug(f"Attempting to acquire lock (force={self.force})")
        # First check for stale lockfiles
        stale_check_result = self._check_and_clean_stale_lock()
        self._debug(f"Stale check result: {stale_check_result}")
        
        if stale_check_result is False:
            # Stale lockfile exists but force is not set - already printed error message
            self._debug("Stale lockfile exists but force not set, returning False")
            return False
        # If stale_check_result is True, lockfile was cleaned or doesn't exist, proceed
        # If stale_check_result is None, lockfile exists and process is running, try to acquire for proper error
            
        try:
            self._debug("Creating lockfile directory if needed")
            # Create lockfile directory if it doesn't exist
            try:
                self.lockfile_path.parent.mkdir(parents=True, exist_ok=True)
            except (IOError, OSError) as e:
                self._debug(f"Cannot create lockfile directory: {e}")
                print(f"ERROR: Cannot create lockfile directory: {self.lockfile_path.parent}")
                print(f"      Error: {e}")
                print(f"      Try running with sudo or use a different lockfile location")
                return False
            
            self._debug(f"Opening lockfile for writing: {self.lockfile_path}")
            # Try to open/create the lockfile
            try:
                self.lockfile = open(self.lockfile_path, 'w')
            except (IOError, OSError) as e:
                if e.errno == errno.EACCES or e.errno == errno.EPERM:
                    self._debug(f"Permission denied opening lockfile: {e}")
                    print(f"ERROR: Permission denied creating lockfile: {self.lockfile_path}")
                    print(f"      Error: {e}")
                    print(f"      Try running with sudo, or the lockfile location is not writable")
                    print(f"      You can also remove the lockfile manually if it exists:")
                    print(f"        sudo rm {self.lockfile_path}")
                    return False
                raise  # Re-raise other errors
            
            self._debug("Attempting to acquire file lock (non-blocking)")
            # Try to acquire exclusive lock (non-blocking)
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError) as e:
                # Close the file before handling the lock error
                if self.lockfile:
                    self.lockfile.close()
                    self.lockfile = None
                
                # Check if another process is holding the lock
                if e.errno == errno.EAGAIN or e.errno == errno.EACCES:
                    self._debug("Lock is held by another process (EAGAIN/EACCES)")
                    # Lock is held by another process - check if it's actually running
                    if self.lockfile_path.exists():
                        try:
                            with open(self.lockfile_path, 'r') as f:
                                pid = f.read().strip()
                            self._debug(f"Read PID from lockfile: {pid}")
                            if pid and self._is_pid_running(pid):
                                print(f"ERROR: Another instance is already running (PID: {pid})")
                                print(f"      Lockfile: {self.lockfile_path}")
                                print(f"      Only one instance can access the Bluetooth adapter at a time.")
                                print(f"      Stop the other instance or service first:")
                                print(f"        systemctl stop govee-exporter.service  # if running as service")
                                print(f"        kill {pid}  # or kill the process directly")
                                print(f"      Or use --force to remove stale lockfile")
                            else:
                                # Stale lock - try to clean it up
                                if self.force:
                                    print(f"Removing stale lockfile (PID {pid} not running)")
                                    self.lockfile_path.unlink()
                                    self._debug("Retrying lock acquisition after removing stale lockfile")
                                    # Try again
                                    return self.acquire()
                                else:
                                    print(f"ERROR: Stale lockfile found (PID {pid} not running)")
                                    print(f"      Lockfile: {self.lockfile_path}")
                                    print(f"      Use --force to remove it and continue")
                        except Exception as e2:
                            self._debug(f"Exception while reading lockfile: {e2}")
                            pass
                    self._debug("Returning False (lock held by another process)")
                    return False
                raise  # Re-raise other errors
            
            self._debug(f"Lock acquired, writing PID {os.getpid()}")
            # Write current PID to lockfile
            self.lockfile.write(str(os.getpid()) + '\n')
            self.lockfile.flush()
            
            self._debug("Lock successfully acquired")
            return True
        except (IOError, OSError) as e:
            self._debug(f"Unexpected exception while acquiring lock: {e} (errno: {e.errno})")
            if self.lockfile:
                self.lockfile.close()
                self.lockfile = None
            print(f"ERROR: Unexpected error acquiring lock: {e}")
            self._debug(f"Returning False (unexpected error: {e})")
            return False
    
    def release(self):
        """Release the lockfile."""
        if self.lockfile:
            try:
                fcntl.flock(self.lockfile.fileno(), fcntl.LOCK_UN)
                self.lockfile.close()
                if self.lockfile_path.exists():
                    self.lockfile_path.unlink()
            except:
                pass
            self.lockfile = None

# ###########################################################################

def influxdb_publish(event, data):
    from influxdb import InfluxDBClient

    if not data:
        print("Not publishing empty data for: ", event)
        return

    try:
        client = InfluxDBClient(host=args.influxdb_host,
                                port=args.influxdb_port,
                                username=args.influxdb_user,
                                password=args.influxdb_pass,
                                database=args.influxdb_db)

#log just the stuff we need
        clean_data = {}
        clean_data['temperature'] = data['temperature']
        clean_data['humidity'] = data['humidity']
        clean_data['battery'] = data['battery']
        clean_data['rssi'] = data['rssi']

        payload = {}
        payload['measurement'] = event

        payload['time']   = int(data['timestamp'])
        payload['fields'] = clean_data

        if args.verbose:
            print ("publishing %s to influxdb [%s:%s]: %s" % (event,args.influxdb_host, args.influxdb_port, payload))

        # write_points() allows us to pass in a precision with the timestamp
        client.write_points([payload], time_precision='s')

    except Exception as e:
        print("Failed to connect to InfluxDB: %s" % e)
        print("  Payload was: %s" % payload)



def twos_complement(n: int, w: int = 16) -> int:
    """Two's complement integer conversion."""
    # Adapted from: https://stackoverflow.com/a/33716541.
    if n & (1 << (w - 1)):
        n = n - (1 << w)
    return n

def restart_bluetooth_service():
    """Attempt to restart bluetooth.service for watchdog recovery."""
    commands = [
        ["systemctl", "restart", "bluetooth.service"],
        ["sudo", "-n", "systemctl", "restart", "bluetooth.service"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print(f"[WATCHDOG] Successfully restarted bluetooth.service using: {' '.join(cmd)}")
                return True
            if args.verbose:
                stderr = result.stderr.strip() if result.stderr else "(no stderr output)"
                print(f"[WATCHDOG DEBUG] Command failed ({' '.join(cmd)}): {stderr}")
        except Exception as e:
            if args.verbose:
                print(f"[WATCHDOG DEBUG] Exception while running {' '.join(cmd)}: {e}")
    print("[WATCHDOG] Failed to restart bluetooth.service (permission denied or command failed)")
    return False

def stop_bluetooth_discovery():
    """Ask BlueZ to release a stuck discovery session."""
    commands = [
        ["bluetoothctl", "--timeout", "5", "scan", "off"],
        ["bluetoothctl", "scan", "off"],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"[WATCHDOG] Stopped BlueZ discovery using: {' '.join(cmd)}")
                return True
            if args.verbose:
                stderr = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
                print(f"[WATCHDOG DEBUG] {' '.join(cmd)} failed: {stderr}")
        except Exception as e:
            if args.verbose:
                print(f"[WATCHDOG DEBUG] Exception while running {' '.join(cmd)}: {e}")
    print("[WATCHDOG] Could not stop BlueZ discovery (session may be held by another process)")
    return False

def log_bluetooth_status():
    """Print adapter and competing-scanner state to diagnose empty scans."""
    print("[BT DEBUG] --- Bluetooth adapter status ---")
    print(f"[BT DEBUG] This process PID: {os.getpid()}")
    print(f"[BT DEBUG] Lockfile in use: {LOCKFILE}")
    for path in (Path("/var/run/goveelog.pid"), Path.home() / ".goveelog.pid"):
        if path.exists():
            try:
                print(f"[BT DEBUG] Lockfile {path} exists, PID={path.read_text().strip()}")
            except Exception as e:
                print(f"[BT DEBUG] Lockfile {path} exists but unreadable: {e}")
        else:
            print(f"[BT DEBUG] Lockfile {path} does not exist")

    try:
        result = subprocess.run(
            ["pgrep", "-af", "goveelog.py|bluetoothctl|hcitool|lescan"],
            capture_output=True, text=True, timeout=5
        )
        procs = [line for line in result.stdout.splitlines() if line.strip()]
        print("[BT DEBUG] Related processes:")
        for line in procs or ["(none)"]:
            print(f"  {line}")
    except Exception as e:
        print(f"[BT DEBUG] Could not list goveelog processes: {e}")

    for unit in ("bluetooth.service", "govee-exporter.service"):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True, text=True, timeout=5
            )
            print(f"[BT DEBUG] {unit}: {result.stdout.strip() or result.stderr.strip() or 'unknown'}")
        except Exception as e:
            print(f"[BT DEBUG] Could not check {unit}: {e}")

    try:
        result = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip() or f"exit {result.returncode}"
            print(f"[BT DEBUG] bluetoothctl show failed: {err}")
        else:
            keys = ("Powered", "Discovering", "Discoverable", "Name", "Alias")
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if any(stripped.startswith(k) for k in keys):
                    print(f"[BT DEBUG] {stripped}")
    except Exception as e:
        print(f"[BT DEBUG] bluetoothctl show exception: {e}")
    print("[BT DEBUG] --- end adapter status ---")

def is_govee_name(name):
    """Govee H5074 uses Govee_*, H5075/H517x often use GVH*."""
    return name.startswith("Govee") or name.startswith("GVH")

def reset_scan_counters():
    """Reset per-scan advertisement counters used by diagnostics and watchdog."""
    detection_callback._all_count = 0
    detection_callback._non_govee_count = 0

def process(mac):
    govee_device = govee_devices[mac]
    if args.raw or args.verbose:
        pprint(govee_device)
    if args.influxdb:
        influxdb_publish(govee_device['name'], govee_device) 

def parse_govee_data(mac, manufacturer_data, rssi):
    global last_packet_timestamp
    data = bytes(manufacturer_data)
    length = len(data)

    # ------------------------------
    # H5074 (new 7-byte format)
    # ------------------------------
    if length == 7:
        temp_raw = int.from_bytes(data[1:3], "little")
        hum_raw  = int.from_bytes(data[3:5], "little")
        batt     = data[5]
        temperature = temp_raw / 100.0
        humidity = hum_raw / 100.0

    # ------------------------------
    # H5074 (legacy 9-byte format)
    # ------------------------------
    elif length == 9 and data[0:2] == b'\xEC\x88':
        raw_temp, hum, batt = unpack_from("<HHB", data, 3)
        temperature = twos_complement(raw_temp) / 100.0
        humidity = hum / 100.0

    # ------------------------------
    # H5179 (11-byte format)
    # ------------------------------
    elif length == 11 and data[0:2] == b'\x88\x01':
        raw_temp, hum, batt = unpack_from("<HHB", data, 6)
        temperature = twos_complement(raw_temp) / 100.0
        humidity = hum / 100.0

    else:
        return

    now = time.time()

    # ensure dict exists, then update values
    if mac not in govee_devices:
        govee_devices[mac] = {}

    govee_devices[mac].update({
        "temperature": temperature,
        "humidity": humidity,
        "battery": batt,
        "rssi": rssi,
        "timestamp": now,
        "last_log": now,
    })
    last_packet_timestamp = now

    process(mac)

def detection_callback(device, advertisement_data):
    mac = device.address
    name = (device.name or "").strip()
    rssi = advertisement_data.rssi
    mfg = advertisement_data.manufacturer_data or {}
    has_govee_mfg = any(mfg_id in GOVEE_MFG_IDS for mfg_id in mfg)

    detection_callback._all_count = getattr(detection_callback, "_all_count", 0) + 1
    if args.verbose and detection_callback._all_count <= 5:
        mfg_ids = ", ".join(f"0x{mfg_id:04X}" for mfg_id in mfg) or "none"
        print(f"[CALLBACK DEBUG] Advertisement #{detection_callback._all_count}: name={name or '(no name)'} mac={mac} rssi={rssi} mfg={mfg_ids}")

    # Accept Govee by name or manufacturer ID (H5074 often splits name and data across ads)
    if not is_govee_name(name) and not has_govee_mfg:
        if args.verbose:
            detection_callback._non_govee_count = getattr(detection_callback, "_non_govee_count", 0) + 1
            if detection_callback._non_govee_count % 100 == 0:
                print(f"[CALLBACK DEBUG] Ignored {detection_callback._non_govee_count} non-Govee devices ({detection_callback._all_count} total advertisements)")
        return

    # Track callback calls per device for debugging
    if args.verbose:
        if not hasattr(detection_callback, "_device_counts"):
            detection_callback._device_counts = {}
        detection_callback._device_counts[mac] = detection_callback._device_counts.get(mac, 0) + 1
        if detection_callback._device_counts[mac] % 10 == 0:
            print(f"[CALLBACK DEBUG] Received {detection_callback._device_counts[mac]} callbacks from {name or '(no name)'} ({mac})")

    # Ensure we have a device record
    if mac not in govee_devices:
        govee_devices[mac] = {
            "address": mac,
            "name": name or mac,
            "last_log": 0,
            "timestamp": 0,
        }
        if args.verbose:
            print(f"Found {name or '(no name)'} ({mac})")
    elif name and govee_devices[mac].get("name") in ("", mac):
        govee_devices[mac]["name"] = name

    # Ignore any packets without manufacturer data
    if not mfg:
        if args.verbose:
            print(f"[CALLBACK DEBUG] {name or '(no name)'} ({mac}) advertisement has no manufacturer data")
        return

    # Process only known Govee manufacturer IDs (0x88EC, 0x0188)
    for mfg_id, data in mfg.items():
        if mfg_id in GOVEE_MFG_IDS:
            parse_govee_data(mac, data, rssi)
        elif args.verbose:
            print(f"[CALLBACK DEBUG] {name or '(no name)'} ({mac}) has unknown manufacturer ID: 0x{mfg_id:04X}")
            
async def main():
    global last_packet_timestamp
    if args.verbose:
        print(f"[MAIN DEBUG] Starting main() function")
        print(f"[MAIN DEBUG] Lockfile path: {LOCKFILE}")
        print(f"[MAIN DEBUG] Force mode: {args.force}")
        print(f"[MAIN DEBUG] Current PID: {os.getpid()}")
    
    # Acquire instance lock
    lock = InstanceLock(LOCKFILE, force=args.force, verbose=args.verbose)
    if args.verbose:
        print(f"[MAIN DEBUG] Attempting to acquire lock...")
    if not lock.acquire():
        if args.verbose:
            print(f"[MAIN DEBUG] Failed to acquire lock, exiting")
        sys.exit(1)
    
    if args.verbose:
        print(f"[MAIN DEBUG] Lock acquired successfully, continuing...")
        log_bluetooth_status()
    
    try:
        print("Starting BLE scan (Ctrl+C to stop)…")
        try:
            # Use scanning_mode="passive" on macOS to avoid CoreBluetooth filtering.
            # On Linux/BlueZ, we must use "active" mode (passive requires or_patterns).
            # To reduce filtering issues, we'll use active mode but rely on the callback
            # to process all advertisements and periodic checks to catch missed devices.
            if platform.system() == "Darwin":
                scanning_mode = "passive"
            else:
                scanning_mode = "active"  # Required on Linux/BlueZ
            
            if args.verbose:
                print(f"Using scanning mode: {scanning_mode} (platform: {platform.system()})")
                print(f"[SCAN DEBUG] Starting BLE scanner - callback will process all advertisements")
            
            max_retries = 3
            retry_delay = 3  # seconds
            attempt = 1
            bt_restart_attempts = 0
            max_bt_restarts = 3
            
            while attempt <= max_retries:
                if args.verbose:
                    print(f"[SCAN DEBUG] Initializing scanner (attempt {attempt}/{max_retries})")
                
                if platform.system() == "Linux":
                    # Give BlueZ a moment to clear any previous discovery state
                    await asyncio.sleep(0.5)

                scanner = BleakScanner(
                    detection_callback,
                    scanning_mode=scanning_mode
                )
                
                try:
                    restart_requested = False
                    restart_bluetooth_needed = False
                    async with scanner:
                        try:
                            # Periodically check discovered devices to ensure we're tracking all Govee devices.
                            # This helps work around BlueZ filtering issues on Linux where advertisements
                            # from one device might stop being reported after a while.
                            last_check = time.time()
                            last_stats = time.time()
                            scan_started_at = last_stats
                            check_interval = 5  # Check every 5 seconds on Linux for more frequent updates
                            stats_interval = 30  # Print statistics every 30 seconds
                            no_ads_timeout = 60  # Adapter is stuck if we see zero BLE ads this long
                            last_packet_timestamp = scan_started_at
                            reset_scan_counters()
                            
                            while True:
                                await asyncio.sleep(1)
                                current_time = time.time()
                                ads_received = getattr(detection_callback, "_all_count", 0)

                                # Watchdog: if we stop receiving packets, restart scanner
                                # and optionally restart bluetooth.service.
                                if args.watchdog_timeout > 0:
                                    time_since_last_packet = current_time - last_packet_timestamp
                                    time_since_scan_start = current_time - scan_started_at
                                    no_ads = ads_received == 0 and time_since_scan_start >= min(no_ads_timeout, args.watchdog_timeout)
                                    no_govee = time_since_last_packet >= args.watchdog_timeout
                                    if no_ads or no_govee:
                                        if no_ads:
                                            print(f"[WATCHDOG] No BLE advertisements at all for {time_since_scan_start:.1f}s")
                                            print("[WATCHDOG] Bluetooth adapter is likely stuck (BlueZ discovery not delivering packets)")
                                            restart_bluetooth_needed = True
                                        else:
                                            print(f"[WATCHDOG] No valid Govee packets for {time_since_last_packet:.1f}s (threshold: {args.watchdog_timeout}s)")
                                            if args.watchdog_restart_bluetooth:
                                                restart_bluetooth_needed = True
                                        print("[WATCHDOG] Stopping current BLE scanner...")
                                        restart_requested = True
                                        break
                                
                                # Print statistics periodically
                                if args.verbose and current_time - last_stats >= stats_interval:
                                    last_stats = current_time
                                    print(f"\n[STATS] Device detection statistics:")
                                    print(f"  Total BLE advertisements this scan: {ads_received}")
                                    print(f"  Non-Govee advertisements ignored: {getattr(detection_callback, '_non_govee_count', 0)}")
                                    if hasattr(detection_callback, '_device_counts'):
                                        for mac, count in detection_callback._device_counts.items():
                                            device_name = govee_devices.get(mac, {}).get('name', 'Unknown')
                                            last_seen = govee_devices.get(mac, {}).get('timestamp', 0)
                                            time_since = current_time - last_seen if last_seen > 0 else float('inf')
                                            print(f"  {device_name} ({mac}): {count} callbacks, last data {time_since:.1f}s ago")
                                    print(f"  Total Govee devices tracked: {len(govee_devices)}")
                                    if ads_received == 0:
                                        print("  HINT: 0 advertisements usually means BlueZ scanning is stuck.")
                                        print("        Run: sudo systemctl restart bluetooth.service")
                                    print()
                                
                                # Periodically verify we're still seeing all Govee devices
                                if current_time - last_check >= check_interval:
                                    last_check = current_time
                                    discovered = scanner.discovered_devices
                                    if args.verbose:
                                        print(f"[SCAN DEBUG] Checking discovered devices ({len(discovered)} total, {ads_received} advertisements)...")
                                    
                                    for device in discovered:
                                        # Ensure all Govee devices are tracked (even if callback missed recent advertisements)
                                        if device.name and is_govee_name(device.name.strip()):
                                            if device.address not in govee_devices:
                                                if args.verbose:
                                                    print(f"Fallback: Found {device.name} ({device.address}) in discovered list")
                                                # Ensure device is tracked (even if callback missed it)
                                                # Note: This won't have manufacturer data, but ensures device is tracked
                                                govee_devices[device.address] = {
                                                    "address": device.address,
                                                    "name": device.name,
                                                    "last_log": 0,
                                                    "timestamp": 0,
                                                }
                                            elif args.verbose:
                                                # Log that we're still seeing this device in discovered list
                                                last_seen = govee_devices[device.address].get('timestamp', 0)
                                                time_since_last = current_time - last_seen
                                                if time_since_last > 30:  # Warn if we haven't seen data in 30 seconds
                                                    print(f"Warning: {device.name} ({device.address}) in discovered list but no recent data (last: {time_since_last:.1f}s ago)")
                                    if args.verbose:
                                        govee_in_discovered = [d for d in discovered if d.name and is_govee_name(d.name.strip())]
                                        print(f"[SCAN DEBUG] Found {len(govee_in_discovered)} Govee devices in discovered list")
                        except KeyboardInterrupt:
                            print("\nStopping scan…")
                    if restart_requested:
                        print("[WATCHDOG] Releasing discovery session...")
                        stop_bluetooth_discovery()
                        await asyncio.sleep(2)
                        if restart_bluetooth_needed:
                            if bt_restart_attempts >= max_bt_restarts:
                                print("[WATCHDOG] Bluetooth restart limit reached; giving up")
                                print("[WATCHDOG] Run: sudo systemctl stop govee-exporter.service; sudo systemctl restart bluetooth.service")
                                sys.exit(1)
                            bt_restart_attempts += 1
                            print("[WATCHDOG] Restarting bluetooth.service...")
                            if not restart_bluetooth_service():
                                print("[WATCHDOG] Could not restart bluetooth.service; scanner restart alone usually fails when discovery is stuck.")
                                print("[WATCHDOG] Run: sudo systemctl restart bluetooth.service")
                            await asyncio.sleep(3)
                        last_packet_timestamp = 0
                        attempt = 1
                        print("[WATCHDOG] Restarting BLE scanner...")
                        continue
                    # Successful scan started; break out of retry loop
                    break
                except Exception as e:
                    error_text = str(e)
                    if "org.bluez.Error.InProgress" in error_text:
                        print("WARNING: Bluetooth adapter reports that a scan is already in progress.")
                        print("         Another process may be using the adapter or a previous scan is still stopping.")
                        if args.verbose:
                            log_bluetooth_status()
                        stop_bluetooth_discovery()
                        if attempt == max_retries:
                            if bt_restart_attempts >= max_bt_restarts:
                                print("ERROR: Max retries reached while trying to start BLE scan.")
                                raise
                            bt_restart_attempts += 1
                            print("[WATCHDOG] Scan still InProgress after retries; restarting bluetooth.service...")
                            restart_bluetooth_service()
                            await asyncio.sleep(3)
                            last_packet_timestamp = 0
                            attempt = 1
                            continue
                        attempt += 1
                        if args.verbose:
                            print(f"[SCAN DEBUG] Waiting {retry_delay}s before retrying scan start…")
                        await asyncio.sleep(retry_delay)
                        continue
                    else:
                        raise
        except Exception as e:
            print(f"ERROR: Failed to initialize BLE scanner: {e}")
            print("       This may happen if:")
            print("       1. Another process is already using the Bluetooth adapter")
            print("       2. Bluetooth adapter is not available or enabled")
            print("       3. Insufficient permissions to access Bluetooth")
            sys.exit(1)
    finally:
        # Always release the lock
        lock.release()

# ###########################################################################
if __name__ == "__main__":
    print("[SCRIPT START] Script execution started")
    print(f"[SCRIPT START] Python version: {sys.version}")
    print(f"[SCRIPT START] Bleak version: {BLEAK_VERSION}")
    print(f"[SCRIPT START] PID: {os.getpid()}")
    
    # argument parsing is u.g.l.y it ain't got no alibi, it's ugly !
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
        """,
    )

    parser.add_argument("-r", "--raw",     dest="raw",     action="store_true", help="print json data to stddout")

    parser.add_argument("--influxdb",      dest="influxdb",      action="store_true",                                 help="publish to influxdb")
    parser.add_argument("--influxdb_host", dest="influxdb_host", action="store",      default="localhost",            help="hostname of InfluxDB HTTP API")
    parser.add_argument("--influxdb_port", dest="influxdb_port", action="store",      default=8086,         type=int, help="hostname of InfluxDB HTTP API")
    parser.add_argument("--influxdb_user", dest="influxdb_user", action="store",                                      help="InfluxDB username")
    parser.add_argument("--influxdb_pass", dest="influxdb_pass", action="store",                                      help="InfluxDB password")
    parser.add_argument("--influxdb_db",   dest="influxdb_db",   action="store",      default="govee",              help="InfluxDB database name")

    parser.add_argument("-v", "--verbose", dest="verbose", action="store_true", help="verbose mode")
    parser.add_argument("--force", dest="force", action="store_true", help="force removal of stale lockfile if process is not running")
    parser.add_argument("--watchdog-timeout", dest="watchdog_timeout", action="store", default=600, type=int, help="seconds without valid Govee packets before scanner recovery (0 disables watchdog)")
    parser.add_argument("--watchdog-restart-bluetooth", dest="watchdog_restart_bluetooth", action="store_true", help="restart bluetooth.service when watchdog timeout occurs")

    print("[SCRIPT START] Parsing arguments...")
    args = parser.parse_args()
    print(f"[SCRIPT START] Arguments parsed - verbose: {args.verbose}, force: {args.force}, watchdog_timeout: {args.watchdog_timeout}, watchdog_restart_bluetooth: {args.watchdog_restart_bluetooth}")

    try:
        print("[SCRIPT START] Calling asyncio.run(main())...")
        asyncio.run(main())
        print("[SCRIPT END] Script completed normally")
    except KeyboardInterrupt:
        print("\n[SCRIPT END] Interrupted by user (Ctrl+C)")
        sys.exit(0)
    except Exception as e:
        print(f"[SCRIPT ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
