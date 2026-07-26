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

TABLE_NAME = "solar_generation_hourly_history"

def clean_record(rec):
    """Normalize dictionary keys to match Supabase schema standards."""
    clean_rec = {}
    for k, v in rec.items():
        # Clean column name: lowercase, strip, replace spaces/dashes
        clean_key = str(k).strip().lower()
        clean_key = clean_key.replace(" (kw)", "").replace(" (kwh)", "").replace(" ", "_").replace("-", "_")
        
        # Skip empty values or NaN
        if pd.isna(v):
            continue
        clean_rec[clean_key] = v
    return clean_rec

def sync_data():
    json_files = glob.glob("*.json")
    csv_files = glob.glob("*.csv") + glob.glob("data/*.csv")
    
    all_records = []
    
    # Process JSON outputs
    for file in json_files:
        if file in ["package.json", "tsconfig.json"]:
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
            records = df.to_dict(orient="records")
            for rec in records:
                all_records.append(clean_record(rec))
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    if not all_records:
        print("No scraped data files found to upload.")
        return

    print(f"Uploading {len(all_records)} total records to Supabase table '{TABLE_NAME}'...")
    
    try:
        res = supabase.table(TABLE_NAME).upsert(all_records).execute()
        print("Successfully synced data to Supabase!")
    except Exception as e:
        print(f"Failed to upsert records into {TABLE_NAME}: {e}")

if __name__ == "__main__":
    sync_data()
