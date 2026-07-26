import os
import sys
import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client

# 1. Load environment variables from .env file
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL or SUPABASE_KEY missing from environment variables.")
    print("Please check your .env file in the root directory.")
    sys.exit(1)

# 2. Initialize Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def sync_data(file_path: str, table_name: str = "solar_data"):
    """Reads a local dataset (CSV/JSON) and syncs it to Supabase."""
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    print(f"Reading solar data from '{file_path}'...")
    
    # Load data into pandas DataFrame
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    elif file_path.endswith(".json"):
        df = pd.read_json(file_path)
    else:
        print("Unsupported file format. Please use .csv or .json.")
        return

    # Convert DataFrame records to Python list of dictionaries
    records = df.to_dict(orient="records")
    print(f"Syncing {len(records)} records to Supabase table '{table_name}'...")

    try:
        # Upsert inserts new rows or updates existing ones matching primary keys
        response = supabase.table(table_name).upsert(records).execute()
        print("Database sync completed successfully!")
        return response.data
    except Exception as e:
        print(f"Sync failed with error: {e}")

if __name__ == "__main__":
    # Update these paths/names if your project uses different file or table names
    DATA_FILE_PATH = "data/solar_data.csv"
    TARGET_TABLE = "solar_data"

    sync_data(DATA_FILE_PATH, TARGET_TABLE)