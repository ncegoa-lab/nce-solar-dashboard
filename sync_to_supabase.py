import os
import json
import glob
import pandas as pd
from supabase import create_client

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Error: SUPABASE_URL or SUPABASE_KEY environment variable missing.")
    exit(1)

supabase = create_client(url, key)

# Pointing directly to your active table from the screenshot
TABLE_NAME = "solar_generation_history"

def clean_record(rec):
    """Normalize dictionary keys to match Supabase schema standard."""
    clean_rec = {}
    
    # Common mapping from scraper fields to DB columns
    field_mapping = {
        "capacity (kw)": "capacity_kw",
        "capacity": "capacity_kw",
        "current power (kw)": "current_power_kw",
        "current_power": "current_power_kw",
        "daily generation (kwh)": "daily_kwh",
        "daily": "daily_kwh",
        "weekly generation (kwh)": "weekly_kwh",
        "weekly": "weekly_kwh",
        "total generation (kwh)": "total_kwh",
        "total": "total_kwh",
        "plant name": "site",
        "system_name": "site",
        "plant_key": "plant_key",
        "brand": "brand",
        "status": "status",
        "date": "date"
    }

    for k, v in rec.items():
        raw_key = str(k).strip().lower()
        
        # Handle lists/arrays to prevent pandas boolean ambiguity
        if isinstance(v, (list, tuple)):
            v = str(v)
        elif pd.isna(v):
            continue

        # Use mapped key if found, otherwise sanitize key name
        clean_key = field_mapping.get(raw_key, raw_key.replace(" ", "_").replace("-", "_"))
        clean_rec[clean_key] = v

    return clean_rec

def sync_data():
    json_files = glob.glob("*.json")
    csv_files = glob.glob("*.csv") + glob.glob("data/*.csv")
    
    all_records = []
    ignore_files = ["package.json", "tsconfig.json", "package-lock.json"]

    # Process JSON outputs
    for file in json_files:
        if file in ignore_files or "probe" in file or "inspection" in file:
            continue
        try:
            with open(file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            all_records.append(clean_record(item))
                elif isinstance(data, dict):
                    all_records.append(clean_record(data))
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    # Process CSV outputs
    for file in csv_files:
        try:
            df = pd.read_csv(file)
            for rec in df.to_dict(orient="records"):
                all_records.append(clean_record(rec))
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    if not all_records:
        print("No valid plant data records found to upload.")
        return

    print(f"Uploading {len(all_records)} records to Supabase table '{TABLE_NAME}'...")
    
    try:
        res = supabase.table(TABLE_NAME).upsert(all_records).execute()
        print("Successfully synced data to Supabase!")
    except Exception as e:
        print(f"Failed to upsert records into {TABLE_NAME}: {e}")

if __name__ == "__main__":
    sync_data()
