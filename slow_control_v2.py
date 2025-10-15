#Connects to both Lakeshore instruments and querys every second, saving the data in mySQL database.
import time
from datetime import datetime
import pyvisa
from pyvisa import VisaIOError
import csv
import os
import mysql.connector
from mysql.connector import Error

# ---------------------------
# Generic LS instrument class
# ---------------------------
class LSInstr:
    def __init__(self, address, model, timeout_ms=5000): #5s timeout
        rm = pyvisa.ResourceManager()
        available = rm.list_resources()
        if address not in available:
            print(f"? No instrument found at {address}. Available: {available}")
            self.conn = None
            return

        try:
            self.conn = rm.open_resource(address)
            self.conn.timeout = timeout_ms
            idn = self.conn.query("*IDN?").strip()
            if model not in idn:
                raise ValueError(f"Instrument at {address} is not {model} (got {idn})")
            self.model = model
            print(f"? Connected to {model} at {address} -> {idn}")
        except VisaIOError as e:
            self.conn = None
            print(f"? Could not open {address}: {e}")

    def get_temp(self, channel):
        if not self.conn:
            return None
        try:
            cmd = "KRDG?" if self.model == "218" else "RDGR?"
            reading = self.conn.query(f"{cmd} {channel}").strip()
            return float(reading)
        except (VisaIOError, ValueError):
            return None


# ---------------------------
# Initialize instruments
# ---------------------------
# Replace addresses with your actual GPIB addresses
ls370 = LSInstr("GPIB0::10::INSTR", model="370")  # LS370
ls218 = LSInstr("GPIB0::12::INSTR", model="218")  # LS218

# Channels to read
channels_370 = [1, 2, 3, 4]  #  channels for LS370
channels_218 = [1, 2, 5, 6, 7]  # channels for LS218

# Polling interval
interval = 1  # seconds

# Data storage
# ---------------------------
# MySQL connection
# ---------------------------
def connect_mysql():
    try:
        return mysql.connector.connect(
            host="localhost",
            user="hep",
            password="hepuser",
            database="experiment_data"
)
    except Error as e:
        print("[!] MySQL connection failed:", e)
        return None

db = connect_mysql()
if not db:
    raise SystemExit("Cannot connect to MySQL. Exiting.")
cursor = db.cursor()

def save_to_db(row):
    query = """
    INSERT INTO dilfridge_log
    (timestamp, LS218_ch1, LS218_ch2, LS218_ch5, LS218_ch6, LS218_ch7)
    VALUES (%s, %s, %s, %s, %s, %s)
    """
    data = (
        row["timestamp"],
        row.get("LS218_ch1"),
        row.get("LS218_ch2"),
        row.get("LS218_ch5"),
        row.get("LS218_ch6"),
        row.get("LS218_ch7"),
    )
    try:
        cursor.execute(query, data)
        db.commit()
    except Error as e:
        print("[!] MySQL insert error:", e)
        db.rollback()

# ---------------------------
# CSV setup
# ---------------------------
# Folder where CSV should be saved
save_folder = r"C:\Users\hep\Documents\vscode_stuff"
os.makedirs(save_folder, exist_ok=True)  # create folder if it doesn't exist

# Prepare CSV file
timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
csv_filename = os.path.join(save_folder, f"test_log_{timestamp_str}.csv")


# Get CSV fieldnames
fieldnames = ["timestamp"]
fieldnames += [f"LS370_ch{ch}" for ch in channels_370]
fieldnames += [f"LS218_ch{ch}" for ch in channels_218]

# Open CSV in append mode
csv_file = open(csv_filename, "w", newline="")
csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
csv_writer.writeheader()

#Data storage in memory
data_log = []
print(f"Logging started. Writing to {csv_filename}")
print("Press Ctrl+C to stop.\n")

# ---------------------------
# Poll loop
# ---------------------------
with open(csv_filename, "w", newline="") as csv_file:
    csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    csv_writer.writeheader()

    try:
        while True:
            timestamp = datetime.now()
            row = {"timestamp": timestamp}

            # LS370 readings
            if ls370.conn:
                for ch in channels_370:
                    val = ls370.get_temp(ch)
                    if val is not None:
                        row[f"LS370_ch{ch}"] = val

            # LS218 readings
            if ls218.conn:
                for ch in channels_218:
                    val = ls218.get_temp(ch)
                    if val is not None:
                        row[f"LS218_ch{ch}"] = val

            # Store in memory
            data_log.append(row)

            # Write row to CSV immediately
            csv_writer.writerow(row)
            csv_file.flush()  # ensure data is saved on disk

            # Print to console
            display = ", ".join(f"{k}={v:.3f}K" for k, v in row.items() if k != "timestamp")
            print(f"[{timestamp:%H:%M:%S}] {display}")

            # Insert into MySQL
            save_to_db(row)

            #cols = ", ".join(row.keys())
            #placeholders = ", ".join(["%s"] * len(row))
            #sql = f"INSERT INTO dilfridge_log ({cols}) VALUES ({placeholders})"
            #values = list(row.values())
            #try:
            #    cursor.execute(sql, values)
            #    db.commit()
            #except Exception as e:
            #    print("MySQL insert error:", e)

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\nPolling stopped by user.")
        print(f"Collected {len(data_log)} data points.")
    finally:
        if db.is_connected():
            cursor.close()
            db.close()
            print("Connections closed.")


print(f"CSV saved as {csv_filename}")
print("Current working directory:", os.getcwd())
print("Saving CSV to:", csv_filename)