
![Github License](https://img.shields.io/github/license/dacarson/WeatherFlowApi) 

# goveelog

## Description
Listen for BTLE broadcast from Govee H5074 or H5179 devices, and publish the data to Influx DB so that it can be graphed with Grafana. To be run as a service.

## System Requirements
- **Bluetooth adapter**: Must be enabled and accessible
- **Python 3**: Tested with Python 3.11+
- **Dependencies**: bleak, influxdb (see `requirements.txt`)

## Setup & Permissions
The script uses the `bleak` library for Bluetooth Low Energy scanning, which typically requires fewer special permissions than raw HCI access. However, you may still need elevated permissions depending on your system configuration.

### Option 1: Run with sudo (if needed)
```bash
sudo python3 goveelog.py [options]
```

### Option 2: Add user to bluetooth group
```bash
sudo usermod -a -G bluetooth $USER
# Log out and back in
```

### Option 3: Set capabilities (if still needed)
```bash
# Find your Python executable
readlink -f $(which python3)

# Set capabilities (replace /usr/bin/python3.11 with your actual path)
sudo setcap 'cap_net_raw,cap_net_admin+eip' /usr/bin/python3.11
```

**Note**: The `bleak` library should work with standard user permissions on most systems. Try running without sudo first.

If the service uses `--watchdog-restart-bluetooth`, the process must be able to run `systemctl restart bluetooth.service` (run the service as root, or add a sudoers rule for that one command).

Parsing logic for the two different types of Govee devices is based on [sensor.goveetemp_bt_hci
](https://github.com/Home-Is-Where-You-Hang-Your-Hack/sensor.goveetemp_bt_hci)

For each device that it hears broadcasting, decodes the device's temperature, humidity, battery and rssi level and logs it at least once per minute.

## Usage
```
usage: goveelog.py [-h] [-r] [--influxdb] [--influxdb_host INFLUXDB_HOST]
                   [--influxdb_port INFLUXDB_PORT] [--influxdb_user INFLUXDB_USER]
                   [--influxdb_pass INFLUXDB_PASS] [--influxdb_db INFLUXDB_DB]
                   [-v] [--force] [--watchdog-timeout SECONDS]
                   [--watchdog-restart-bluetooth]

optional arguments:
  -h, --help            show this help message and exit
  -r, --raw             print raw data to stdout
  --influxdb            publish to influxdb
  --influxdb_host INFLUXDB_HOST
                        hostname of InfluxDB HTTP API (default: localhost)
  --influxdb_port INFLUXDB_PORT
                        port of InfluxDB HTTP API (default: 8086)
  --influxdb_user INFLUXDB_USER
                        InfluxDB username
  --influxdb_pass INFLUXDB_PASS
                        InfluxDB password
  --influxdb_db INFLUXDB_DB
                        InfluxDB database name (default: govee)
  -v, --verbose         verbose mode - show device discovery and data
  --force               remove a stale lockfile if that process is not running
  --watchdog-timeout SECONDS
                        seconds without valid Govee packets before scanner recovery
                        (default: 600; 0 disables)
  --watchdog-restart-bluetooth
                        restart bluetooth.service when the watchdog fires
```

Only one instance should scan at a time. The script uses a lockfile at `/var/run/goveelog.pid` when writable, otherwise `~/.goveelog.pid`. Use `--force` to clear a stale lock after a crash.

To configure a service, create `/etc/systemd/system/govee-exporter.service`:
```
[Unit]
Description=Govee BLE Exporter
After=network.target bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
WorkingDirectory=/home/pi/Govee-exporter
ExecStartPre=/usr/sbin/rfkill unblock bluetooth
ExecStartPre=/usr/bin/hciconfig hci0 up
ExecStart=/home/pi/Govee-exporter/venv/bin/python goveelog.py --influxdb --force --watchdog-timeout 300 --watchdog-restart-bluetooth
Restart=always
RestartSec=10
TimeoutStopSec=15
KillSignal=SIGTERM
StandardOutput=append:/home/pi/Govee-exporter/goveelog.log
StandardError=append:/home/pi/Govee-exporter/goveelog.log

[Install]
WantedBy=multi-user.target
```
Adjust `WorkingDirectory`, `ExecStart`, and InfluxDB options for your install. `--watchdog-restart-bluetooth` needs permission to restart `bluetooth.service`.

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl start govee-exporter
sudo systemctl enable govee-exporter
sudo systemctl status govee-exporter
```

Watch the log with `tail -f /home/pi/Govee-exporter/goveelog.log` or `journalctl -u govee-exporter -f`.

## Troubleshooting

### Common Issues

**Permission denied accessing Bluetooth adapter**
- The script uses the `bleak` library which typically requires fewer permissions
- Try running without sudo first, then use the permission setup options if needed
- Check that Bluetooth is enabled: `bluetoothctl show` (`Powered: yes`)

**Service fails to start**
- Check service status: `systemctl status govee-exporter`
- View logs: `journalctl -u govee-exporter -f` or the log file above
- Ensure the user has Bluetooth access and the venv path in `ExecStart` is correct
- Another instance may be holding a lockfile: `ls /var/run/goveelog.pid ~/.goveelog.pid`

**Scanner starts but finds 0 devices / 0 advertisements**
- This usually means BlueZ is stuck in `Discovering: yes` from a previous scan (for example after systemd sent SIGTERM)
- Check: `bluetoothctl show` — if `Discovering: yes` before the script starts, stop other scanners and restart Bluetooth:
  ```bash
  sudo systemctl stop govee-exporter.service
  sudo rm -f /var/run/goveelog.pid /home/pi/.goveelog.pid
  sudo systemctl restart bluetooth.service
  sudo systemctl start govee-exporter.service
  ```
- Run with `-v` and confirm you see `[CALLBACK DEBUG] Advertisement #1` within a few seconds
- The watchdog restarts the scanner after 60s of zero advertisements, and restarts `bluetooth.service` if `--watchdog-restart-bluetooth` is set

**No Govee data being collected (but other BLE ads appear)**
- Check that Govee devices are broadcasting (batteries not dead)
- Use `-v` to see device names and manufacturer IDs
- H5074 devices typically show as `Govee_H5074_XXXX`

**InfluxDB connection issues**
- Verify InfluxDB is running and accessible
- Check credentials and database name
- Test connection manually: `curl http://localhost:8086/ping`

This content is licensed under [MIT License](https://opensource.org/license/mit/)
  
