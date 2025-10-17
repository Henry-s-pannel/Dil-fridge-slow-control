import time
from datetime import datetime
import pyvisa
from pyvisa import VisaIOError
import mysql.connector
from mysql.connector import Error
import os
import sys
import logging
import serial
from datetime import datetime, timezone
import re
# -----------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------
#save data in UTC time



# MySQL database connection settings
MYSQL_CONFIG = {
    "host": "localhost",
    "user": "hep",
    "password": "hepuser",
    "database": "experiment_data",
}
# GPIB address for the LS218 instrument
LS218_ADDRESS = "GPIB0::12::INSTR"

# Polling interval in seconds
POLL_INTERVAL = 5  # seconds

# Channels to read from LS218
CHANNELS = [1, 2, 5, 6, 7]

# Channels to read from pressure gauges (MKS2000)
MKS_PORTS = ["COM6", "COM7", "COM8"]
# Log file path
LOG_FILE = r"C:\Users\hep\Documents\vscode_stuff\ls218_logger_service.log"

# -----------------------------------------------------------
# LOGGING
# -----------------------------------------------------------
# Configure logging to file and console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

# -----------------------------------------------------------
# CLASS DEFINITIONS
# -----------------------------------------------------------
class LS218:
    """
    Class to handle communication with the LS218 temperature controller.
    """
    def __init__(self, address):
        self.address = address
        self.rm = pyvisa.ResourceManager()
        self.conn = None
        self.connect()

    def connect(self):
        """Try to connect to the LS218 instrument."""
        try:
             # Check if the instrument is available
            if self.address not in self.rm.list_resources():
                raise VisaIOError(-1073807343, "Device not found")
            # Open the connection
            self.conn = self.rm.open_resource(self.address)
            self.conn.timeout = 5000
            # Query instrument identity
            idn = self.conn.query("*IDN?").strip()
            if "218" not in idn:
                raise ValueError(f"Unexpected IDN: {idn}")
            logging.info(f"Connected to LS218: {idn}")
        except Exception as e:
            logging.warning(f"LS218 connection failed: {e}")
            self.conn = None

    def get_all_temps(self):
        """
        Query all temperature channels in a single atomic operation.
        Returns a dictionary of {channel: temperature}.
        """
        if self.conn is None:
            self.connect()
            if self.conn is None:
                # Return a dictionary with None values if connection fails
                return {ch: None for ch in CHANNELS}
        try:
            # Clear the instrument buffer before querying to remove any stale data.
            self.conn.clear()
            
            # "KRDG? 0" queries all 8 channels at once.
            # Response is a comma-separated string, e.g., "+12.345,+50.678,..."
            response = self.conn.query("KRDG? 0").strip()
            
            # Split the response and convert to floats
            readings = [float(val) for val in response.split(',')]
            
            # Return a dictionary mapping channel number (1-8) to its reading
            return {i + 1: readings[i] for i in range(len(readings))}
        
        except (VisaIOError, ValueError) as e:
            logging.warning(f"LS218 read all channels error: {e}")
            self.conn = None  # Force reconnect on next cycle
            return {ch: None for ch in CHANNELS}



class MKS2000:
    
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.connect()

    def connect(self):
        try:
            self.ser = serial.Serial(self.port, baudrate=self.baudrate, timeout=self.timeout)
            print(f"? Connected to MKS2000 on {self.port}")
        except Exception as e:
            print(f"MKS2000 connection error on {self.port}: {e}")
            self.ser = None

    def get_pressures(self):
        if not self.ser or not self.ser.is_open:
            self.connect()
            if not self.ser:
                return None
        try:
            self.ser.reset_input_buffer()
            self.ser.write(b"p")  # send ASCII 112
            time.sleep(0.1)
            resp = self.ser.readline().decode(errors="replace").strip()
            if not resp:
                return None

            # Normalize any Unicode minus signs to ASCII hyphen
            bad_minus = [
                '\u2212',  # minus sign
                '\u2013',  # en dash
                '\u2014',  # em dash
                '\u2012',  # figure dash
                '\u2010',  # hyphen
                '\u2011',  # non-breaking hyphen
            ]
            for bm in bad_minus:
                resp = resp.replace(bm, '-')

            # Example responses: "- 3.7e+0 Off" or "Off 340.2e+0"
            parts = resp.split()
            numeric = None
            for p in parts:
                # pick the first part that contains digits
                if any(ch.isdigit() for ch in p):
                    numeric = p
                    break

            if numeric is None:
                return None

            # Check if the original response started with a minus
            if resp.strip().startswith('-') and not numeric.startswith('-'):
                numeric = '-' + numeric

            # Convert to float
            try:
                pressure = float(numeric)
            except ValueError:
                logging.warning(f"Could not parse pressure from '{resp}'")
                return None

            return pressure

        except Exception as e:
            logging.warning(f"MKS2000 read error on {self.port}: {e}")
            self.ser = None
            return None



 

class MySQLLogger:

    def __init__(self, config):
        self.config = config
        self.conn = None
        self.cursor = None
        self.connect()

    def connect(self):
        """Try to (re)connect to MySQL."""
        while True:
            try:
                self.conn = mysql.connector.connect(**self.config)
                self.cursor = self.conn.cursor()
                logging.info("Connected to MySQL.")
                break
            except Error as e:
                logging.warning(f"MySQL connection error: {e}")
                time.sleep(5)

    def insert(self, row):
        cols = ", ".join(row.keys())
        placeholders = ", ".join(["%s"] * len(row))
        sql = f"INSERT INTO dilfridge_log ({cols}) VALUES ({placeholders})"
        values = list(row.values())
        try:
            self.cursor.execute(sql, values)
            self.conn.commit()
        except Error as e:
            logging.warning(f"MySQL insert error: {e}")
            self.connect()  # reconnect on failure
        


# -----------------------------------------------------------
# MAIN LOOP
# -----------------------------------------------------------
def main(mks_devices):
    ls218 = LS218(LS218_ADDRESS)
    #mks_devices = [MKS2000(p) for p in MKS_PORTS]
    db = MySQLLogger(MYSQL_CONFIG)

    logging.info("Starting LS218 + MKS2000 polling service... (Ctrl+C to stop)")

    while True:
        timestamp = datetime.now(timezone.utc)
        row = {"timestamp": timestamp}

        # LS218 readings
        # Get all temperatures at once
        all_temps = ls218.get_all_temps()

        # Populate the row with the specific channels you care about
        for ch in CHANNELS:
            row[f"LS218_ch{ch}"] = all_temps.get(ch) # .get(ch) safely returns None if key is missing

        # MKS2000 readings
        for i, mks in enumerate(mks_devices, start=1):  
            row[f"MKS{i}_g1"] =  mks.get_pressures()
        

        # Print to console
        display = ", ".join(f"{k}={v:.3f}" for k, v in row.items() if k != "timestamp" and v is not None)
        logging.info(f"[{timestamp:%H:%M:%S}] {display}")

        # Insert into DB
        db.insert(row)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    mks_devices_main = [MKS2000(p) for p in MKS_PORTS]
    try:
        # Capture the mks_devices list to close them later
        main(mks_devices_main)
    except KeyboardInterrupt:
        logging.info("Service stopped by user.")
    finally:
        # Correctly close the serial ports
        logging.info("Closing MKS serial ports.")
        for device in mks_devices_main:
            if device.ser and device.ser.is_open:
                device.ser.close()
