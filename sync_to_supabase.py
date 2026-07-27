import os
import json
import glob
from datetime import datetime
import pandas as pd
from supabase import create_client

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")

if not url or not key:
    print("Error: SUPABASE_URL or SUPABASE_KEY environment variable missing.")
    exit(1)

supabase = create_client(url, key)

TABLE_NAME = "solar_generation_history"

# All exact column names visible in your Supabase table screenshots
VALID_DB_COLUMNS = {
    "date", "brand", "site", "plant_key", "status",
    "capacity", "daily", "weekly", "year", "total", "cuf", "current_power"
}

def clean_record(rec, default_brand="Unknown", default_site="Unknown Site", default_key="unknown_key"):
    """Map incoming JSON/CSV fields directly to Supabase metric columns."""
    clean_rec = {}
    
    field_mapping = {
        # String/Ident fields
        "plant name": "site",
        "plant_name": "site",
        "system_name": "site",
        "station_name": "site",
        "name": "site",
        "site": "site",
        "brand": "brand",
        "status": "status",
        "date": "date",
        "time": "date",
        "timestamp": "date",
        "plant_key": "plant_key",
        "plant_id": "plant_key",
        "station_id": "plant_key",
        "key": "plant_key",
        
        # Metric field mappings matching your table headers
        "capacity (kw)": "capacity",
        "capacity_kw": "capacity",
        "capacity": "capacity",
        "daily generation (kwh)": "daily",
        "daily_kwh": "daily",
        "daily": "daily",
        "weekly generation (kwh)": "weekly",
        "weekly_kwh": "weekly",
        "weekly": "weekly",
        "year generation (kwh)": "year",
        "year_kwh": "year",
        "year": "year",
        "total generation (kwh)": "total",
        "total_kwh": "total",
        "total": "total",
        "cuf (%)": "cuf",
        "cuf": "cuf",
        "current power (kw)": "current_power",
        "current_power_kw": "current_power",
        "current_power": "current_power"
    }

    for k, v in rec.items():
        raw_key = str(k).strip().lower()
        
        if isinstance(v, (list, tuple)):
            v = str(v)
        elif pd.isna(v):
            continue

        clean_key = field_mapping.get(raw_key, raw_key)
        
        if clean_key in VALID_DB_COLUMNS:
            # Cast numerical metrics to float/int where possible
            if clean_key in {"capacity", "daily", "weekly", "year", "total", "cuf", "current_power"}:
                try:
                    clean_rec[clean_key] = float(v)
                except (ValueError, TypeError):
                    clean_rec[clean_key] = v
            else:
                clean_rec[clean_key] = v

    # Fallbacks for NOT NULL constraints
    if not clean_rec.get("brand") or pd.isna(clean_rec.get("brand")):
        clean_rec["brand"] = default_brand

    if not clean_rec.get("site") or pd.isna(clean_rec.get("site")):
        clean_rec["site"] = default_site

    if not clean_rec.get("plant_key") or pd.isna(clean_rec.get("plant_key")):
        clean_rec["plant_key"] = default_key

    if not clean_rec.get("date") or pd.isna(clean_rec.get("date")):
        clean_rec["date"] = datetime.now().strftime("%Y-%m-%d")

    return clean_rec

def deduplicate_records(records):
    """Ensure no duplicate (plant_key, date) combinations exist in the upload batch."""
    deduped = {}
    for rec in records:
        key = (rec["plant_key"], rec["date"])
        deduped[key] = rec
    return list(deduped.values())

def sync_data():
    json_files = glob.glob("*.json")
    csv_files = glob.glob("*.csv") + glob.glob("data/*.csv")
    
    all_records = []
    ignore_files = ["package.json", "tsconfig.json", "package-lock.json"]

    # Process JSON outputs
    for file in json_files:
        if file in ignore_files or "probe" in file or "inspection" in file:
            continue
        
        brand_guess = file.split("_")[0].capitalize()
        site_guess = file.replace(".json", "").replace("_", " ").title()
        key_guess = file.replace(".json", "")
        
        try:
            with open(file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            cleaned = clean_record(item, default_brand=brand_guess, default_site=site_guess, default_key=key_guess)
                            if cleaned:
                                all_records.append(cleaned)
                elif isinstance(data, dict):
                    cleaned = clean_record(data, default_brand=brand_guess, default_site=site_guess, default_key=key_guess)
                    if cleaned:
                        all_records.append(cleaned)
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    # Process CSV outputs
    for file in csv_files:
        base_name = os.path.basename(file)
        brand_guess = base_name.split("_")[0].capitalize()
        site_guess = base_name.replace(".csv", "").replace("_", " ").title()
        key_guess = base_name.replace(".csv", "")
        
        try:
            df = pd.read_csv(file)
            for rec in df.to_dict(orient="records"):
                cleaned = clean_record(rec, default_brand=brand_guess, default_site=site_guess, default_key=key_guess)
                if cleaned:
                    all_records.append(cleaned)
            print(f"Loaded records from {file}")
        except Exception as e:
            print(f"Error reading {file}: {e}")

    if not all_records:
        print("No valid records found.")
        return

    unique_records = deduplicate_records(all_records)

    print(f"Uploading {len(unique_records)} records with generation data to Supabase table '{TABLE_NAME}'...")
    
    try:
        res = supabase.table(TABLE_NAME).upsert(unique_records, on_conflict="plant_key,date").execute()
        print("Successfully synced generation data to Supabase!")
    except Exception as e:
        print(f"Failed to upsert records into {TABLE_NAME}: {e}")

if __name__ == "__main__":
    sync_data()
