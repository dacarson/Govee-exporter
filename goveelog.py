#!/usr/bin/python3

import argparse
import time
from bleson import get_provider, Observer
from bleson.logger import log, set_level, ERROR, DEBUG
import json
from pprint import pprint
from struct import unpack_from

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

# Disable warnings
set_level(ERROR)

# # Uncomment for debug log level
# set_level(DEBUG)

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

# Govee parsing is based on: https://github.com/Home-Is-Where-You-Hang-Your-Hack/sensor.goveetemp_bt_hci

# On BLE advertisement callback
def on_advertisement(advertisement):
    log.debug(advertisement)

    mac = advertisement.address.address
    if mac in govee_devices and advertisement.mfg_data is not None:
        prefix = int(advertisement.mfg_data.hex()[0:4],16)   
 
        # H5074 have mfg_data length of 9 
        if prefix == 0x88EC and len(advertisement.mfg_data) == 9:
            raw_temp, hum, batt = unpack_from("<HHB", advertisement.mfg_data, 3)
            govee_devices[mac]["temperature"] = float(twos_complement(raw_temp) / 100.0)
            govee_devices[mac]["humidity"] = float(hum / 100.0)
            govee_devices[mac]["battery"] = int(batt)
            govee_devices[mac]["timestamp"] = time.time()

            if advertisement.rssi is not None and advertisement.rssi != 0:
                govee_devices[mac]["rssi"] = advertisement.rssi

        # H5179 have mfg_data length of 11 
        if prefix == 0x0188 and len(advertisement.mfg_data) == 11:
            raw_temp, hum, batt = unpack_from("<HHB", advertisement.mfg_data, 6)
            govee_devices[mac]["temperature"] = float(twos_complement(raw_temp) / 100.0)
            govee_devices[mac]["humidity"] = float(hum / 100.0)
            govee_devices[mac]["battery"] = int(batt)
            govee_devices[mac]["timestamp"] = time.time()

            if advertisement.rssi is not None and advertisement.rssi != 0:
                govee_devices[mac]["rssi"] = advertisement.rssi

        time_now = time.time()
        if time_now - govee_devices[mac]["last_log"] > log_interval and govee_devices[mac]["timestamp"] > 0:
            process(mac)
            govee_devices[mac]["last_log"] = time_now 

    if advertisement.name is not None and advertisement.name.startswith("Govee"):
        if mac not in govee_devices:
            govee_devices[mac] = {}
            name = advertisement.name.split("'")[0]
            govee_devices[mac]["address"] = mac
            govee_devices[mac]["name"] = name
            govee_devices[mac]["last_log"] = 0
            govee_devices[mac]["timestamp"] = 0
            if args.verbose:
                print("Found " + name)
    
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

    adapter = get_provider().get_adapter()
    observer = Observer(adapter)
    observer.on_advertising_data = on_advertisement

    try:
        while True:
            observer.start()
            time.sleep(0.5)
            observer.stop()

    except KeyboardInterrupt:
        terminate()