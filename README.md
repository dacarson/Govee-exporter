
![Github License](https://img.shields.io/github/license/dacarson/WeatherFlowApi) 

# goveelog

## Description
Listen for BTLE broadcast from Govee H5074 or H5179 devices, and publish the data to Influx DB so that it can be graphed with Grafana. To be run as a service.

## System Requirements
- **Bluetooth adapter**: Must be enabled and accessible
- **Permissions**: Requires elevated privileges to access Bluetooth HCI interface
- **Python 3**: Tested with Python 3.11+
- **Dependencies**: bleson, influxdb-client

## Setup & Permissions
Due to Bluetooth security restrictions, this script requires elevated permissions. Choose one of these approaches:

### Option 1: Run with sudo (simplest)
```bash
sudo python3 goveelog.py [options]
```

### Option 2: Set capabilities (recommended for services)
```bash
# Find your Python executable
readlink -f $(which python3)

# Set capabilities (replace /usr/bin/python3.11 with your actual path)
sudo setcap 'cap_net_raw,cap_net_admin+eip' /usr/bin/python3.11
```

### Option 3: Add user to bluetooth group + capabilities
```bash
sudo usermod -a -G bluetooth $USER
# Log out and back in, then set capabilities as above
```

**Note**: If you get "Permission denied" errors, the script will now provide clear error messages explaining the issue.

Parsing logic for the two different types of Govee devices is based on [sensor.goveetemp_bt_hci
](https://github.com/Home-Is-Where-You-Hang-Your-Hack/sensor.goveetemp_bt_hci)

For each device that it hears broadcasting, decodes the device's temperature, humidity, battery and rssi level and logs it at least once per minute.

## Usage
```
usage: goveelog.py [-h] [-r] [--influxdb] [--influxdb_host INFLUXDB_HOST] [--influxdb_port INFLUXDB_PORT] 
                        [--influxdb_user INFLUXDB_USER] [--influxdb_pass INFLUXDB_PASS] [--influxdb_db INFLUXDB_DB] 
                        [-v]

optional arguments:
  -h, --help            show this help message and exit
  -r, --raw             print json data to stddout
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
  -v, --verbose         verbose mode - show threads
  ````

To configure a service, create the file `/etc/systemd/system/govee.service` and insert the following information:
```
[Unit]
Description=Govee Influxdb service
After=network.target
StartLimitIntervalSec=0

[Service]
Type=simple
Restart=always
RestartSec=1
User=<user to run as>
ExecStart=/usr/bin/python3 /full/path/to/goveelog.py --influxdb --influxdb_user <infuxdb username> --influxdb_pass <influxdb password>

[Install]
WantedBy=multi-user.target
```
Make sure to specify the User to run as, the path to goveelog.py and the username and password for influxdb.


Then run the command to start the service: `systemctl start govee`.

To make the service start automatically on boot, run the command `systemctl enable govee`.

At anytime, check the status of the service with: `systemctl status govee`.

## Troubleshooting

### Common Issues

**Permission denied accessing Bluetooth adapter**
- The script requires elevated privileges to access Bluetooth
- Use one of the permission setup options above
- Check that Bluetooth is enabled: `bluetoothctl show`

**Service fails to start**
- Check service status: `systemctl status govee`
- View logs: `journalctl -u govee -f`
- Ensure the user specified in the service file has proper permissions

**No data being collected**
- Verify Bluetooth is working: `bluetoothctl scan on`
- Check that Govee devices are broadcasting (batteries not dead)
- Use `-v` flag for verbose output to debug

**InfluxDB connection issues**
- Verify InfluxDB is running and accessible
- Check credentials and database name
- Test connection manually: `curl http://localhost:8086/ping`

This content is licensed under [MIT License](https://opensource.org/license/mit/)
  
