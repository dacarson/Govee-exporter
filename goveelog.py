#!/usr/bin/python3

import argparse
import asyncio
import time
from pprint import pprint
from struct import unpack_from
from bleak import BleakScanner

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

def process(mac):
    govee_device = govee_devices[mac]
    if args.raw or args.verbose:
        pprint(govee_device)
    if args.influxdb:
        influxdb_publish(govee_device['name'], govee_device) 

def parse_govee_data(mac, manufacturer_data, rssi):
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

    process(mac)

def detection_callback(device, advertisement_data):
    mac = device.address
    name = (device.name or "").strip()
    rssi = advertisement_data.rssi

    # Process only devices whose name starts with "Govee"
    if not name.startswith("Govee"):
        return

    # Ensure we have a device record
    if mac not in govee_devices:
        govee_devices[mac] = {
            "address": mac,
            "name": name,
            "last_log": 0,
            "timestamp": 0,
        }
        if args.verbose:
            print(f"Found {name} ({mac})")

    # Ignore any packets without manufacturer data
    if not advertisement_data.manufacturer_data:
        return

    # Process only known Govee manufacturer IDs (0x88EC, 0x0188)
    for mfg_id, data in advertisement_data.manufacturer_data.items():
        if mfg_id in (0xEC88, 0x88EC, 0x0188):
            parse_govee_data(mac, data, rssi)
            
async def main():
    print("Starting BLE scan (Ctrl+C to stop)…")
    async with BleakScanner(detection_callback) as scanner:
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping scan…")

# ###########################################################################
if __name__ == "__main__":

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

    args = parser.parse_args()

    asyncio.run(main())
