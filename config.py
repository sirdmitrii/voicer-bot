import os
import json

# ==========================================
# CONFIGURATION
# ==========================================

CREDENTIALS_FILE = "credentials.json"

# Load keys from credentials file
try:
    with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
        creds = json.load(f)
except FileNotFoundError:
    print(f"❌ Error: {CREDENTIALS_FILE} not found!")
    creds = {}

TELEGRAM_TOKEN = creds.get("telegram_token")
OPENAI_API_KEY = creds.get("openai_api_key")
GOOGLE_SHEET_NAME = creds.get("google_sheet_name")
GOOGLE_CREDS_FILE = CREDENTIALS_FILE
