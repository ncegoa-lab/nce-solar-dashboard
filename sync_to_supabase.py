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

# Define target table in Supabase
TABLE_NAME = "solar_generation_hourly_history"

def sync_data():
    json_files = glob.glob("*.json")
    csv_files = glob.glob("*.csv") + glob.glob("data/*.csv")
    
    all_records = []
    
    # Process JSON outputs (Solis, SolaX, etc.)
    for file in json_files:
        if file in ["package.json", "tsconfig.json"]:  # Skip config files
            continue
        try:
            with open(file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    all_records.extend(data)
                elif isinstance(data, dict):
                    all_records.append(data)
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    # Process CSV outputs
    for file in csv_files:
        try:
            df = pd.read_csv(file)
            all_records.extend(df.to_dict(orient="records"))
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    if not all_records:
        print("No scraped data files found to upload.")
        return

    print(f"Uploading {len(all_records)} total records to Supabase table '{TABLE_NAME}'...")
    
    # Batch upsert to Supabase
    try:
        res = supabase.table(TABLE_NAME).upsert(all_records).execute()
        print("Successfully synced data to Supabase!")
    except Exception as e:
        print(f"Failed to upsert records into {TABLE_NAME}: {e}")

if __name__ == "__main__":
    sync_data()
