import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd
import requests
import zipfile
import io
from datetime import datetime, timedelta
import os
import json

# --- Helper Functions ---
def safe_json_parse(text, source_name="API"):
    """Safely parse JSON with detailed error handling"""
    if not text or not text.strip():
        raise ValueError(f"ERROR: {source_name} returned an empty or null response.")
    
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"ERROR: Failed to parse JSON from {source_name}")
        # Log first 200 chars for debugging (secrets should be handled carefully)
        preview = text[:200] if len(text) > 200 else text
        print(f"Response content preview: {preview}")
        raise ValueError(f"Invalid JSON format from {source_name}: {e}")

# --- 1. Environment Validation & Credentials Setup ---
print("Initializing script and validating environment...")

gcp_creds_raw = os.getenv('GCP_CREDENTIALS')
if not gcp_creds_raw:
    print("CRITICAL ERROR: GCP_CREDENTIALS environment variable is not set.")
    exit(1)

try:
    creds_dict = safe_json_parse(gcp_creds_raw, "GCP_CREDENTIALS Secret")
    
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    print("Google Sheets API authorized successfully.")
except Exception as e:
    print(f"CRITICAL ERROR during initialization: {e}")
    exit(1)

# अपनी गूगल शीट की ID यहाँ डालें
spreadsheet_id = "1c0IsT-KcWlDHcVxkgJvScxmTEvLjpqaxAH3S5UPT8ts" 
try:
    worksheet = client.open_by_key(spreadsheet_id).worksheet("Top 250 Stocks")
except Exception as e:
    print(f"ERROR: Could not open worksheet. Check spreadsheet ID and permissions. {e}")
    exit(1)

# --- 2. NSE UDiFF Data Fetcher ---
def fetch_bhavcopy_for_date(date_obj):
    date_str = date_obj.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            if not response.content:
                print(f"Empty response from NSE for {date_str}")
                return None
                
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_filename = z.namelist()[0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f)
                    
                    # Column Mapping (Handling both old and new formats)
                    sym_col = 'TckrSymb' if 'TckrSymb' in df.columns else 'SYMBOL'
                    close_col = 'ClsPric' if 'ClsPric' in df.columns else 'CLOSE'
                    series_col = 'SctySrs' if 'SctySrs' in df.columns else 'SERIES'
                    
                    vol_col = 'TtlTradgVol'
                    for c in ['TtlTradgVol', 'TtlTrdQty', 'TotTrdQty', 'TOTTRDQTY']:
                        if c in df.columns:
                            vol_col = c
                            break
                    
                    # Filter EQ Series and Exclude ETFs/Bees
                    if series_col in df.columns:
                        df = df[df[series_col].astype(str).str.strip() == 'EQ']
                    
                    filter_keywords = 'BEES|ETF|GOLD|LIQUID|CASE|SILVER|LIQ'
                    df = df[~df[sym_col].astype(str).str.contains(filter_keywords, case=False, na=False)]
                    
                    df_top = df.sort_values(by=vol_col, ascending=False).head(250)
                    return df_top[[sym_col, vol_col, close_col]].values.tolist()
        else:
            print(f"NSE Info: Status {response.status_code} for {date_str}")
        return None
    except Exception as e:
        print(f"NSE Error fetching data for {date_str}: {e}")
        return None

# --- 3. Execution Logic ---
date = datetime.now()
data_to_insert = None
fetched_date_str = ""

print("Searching for the latest available NSE data...")
for i in range(5): 
    test_date = date - timedelta(days=i)
    if test_date.weekday() >= 5: # Skip weekends
        continue
        
    data_to_insert = fetch_bhavcopy_for_date(test_date)
    if data_to_insert:
        fetched_date_str = test_date.strftime('%d-%b-%Y')
        print(f"SUCCESS: Data found for {fetched_date_str}")
        break
    else:
        print(f"No data found for {test_date.strftime('%Y-%m-%d')}, checking previous day...")

# --- 4. Update Sheet ---
if data_to_insert:
    try:
        print(f"Updating Google Sheet with {len(data_to_insert)} records...")
        worksheet.batch_clear(['A2:C251'])
        worksheet.update('A2', data_to_insert)
        
        # Update Timestamp in K2
        ist_now = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime('%d-%b %H:%M')
        status_msg = f"Data Date: {fetched_date_str} | Last Update: {ist_now} (IST)"
        worksheet.update('K2', [[status_msg]])
        
        print("🎉 SUCCESS: Google Sheet updated successfully!")
    except Exception as e:
        print(f"CRITICAL ERROR updating sheet: {e}")
        exit(1)
else:
    print("CRITICAL ERROR: No data could be fetched for the last 5 days. Check NSE availability.")
    exit(1)
