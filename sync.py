import os
import time
import json
import logging
import datetime
import fitbit
import schedule
import requests
from garminconnect import Garmin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN_FILE = '/app/data/fitbit_token.json'

class FitbitClient:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = None
        self.load_client()

    def load_client(self):
        if not os.path.exists(TOKEN_FILE):
            logger.error(f"Token file not found at {TOKEN_FILE}. Please run get_fitbit_token.py first.")
            raise Exception("Fitbit token not found")

        with open(TOKEN_FILE, 'r') as f:
            token = json.load(f)

        self.client = fitbit.Fitbit(
            self.client_id,
            self.client_secret,
            access_token=token.get('access_token'),
            refresh_token=token.get('refresh_token'),
            expires_at=token.get('expires_at'),
            refresh_cb=self.refresh_callback
        )

    def refresh_callback(self, token):
        """Called when the token is refreshed. Save it."""
        logger.info("Fitbit token refreshed. Saving...")
        with open(TOKEN_FILE, 'w') as f:
            json.dump(token, f)

    def get_weight_logs(self, start_date):
        """
        Fetch weight logs from start_date (YYYY-MM-DD) to today.
        Fitbit API allows fetching body/log/weight/date/[base-date]/[end-date].json
        """
        try:
            today = datetime.date.today().strftime("%Y-%m-%d")
            data = self.client.get_bodyweight(base_date=start_date, end_date=today)
            return data.get('weight', [])
        except Exception as e:
            logger.error(f"Error fetching Fitbit data: {e}")
            raise

def sync_data():
    logger.info("Starting synchronization process...")
    
    fitbit_id = os.environ.get('FITBIT_CLIENT_ID')
    fitbit_secret = os.environ.get('FITBIT_CLIENT_SECRET')
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    start_date_str = os.environ.get('SYNC_START_DATE')
    
    if not all([fitbit_id, fitbit_secret, garmin_email, garmin_password]):
        logger.error("Missing environment variables.")
        return

    # Default start date: yesterday if not specified
    if not start_date_str:
        start_date_str = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    
    try:
        # Fitbit
        fb = FitbitClient(fitbit_id, fitbit_secret)
        weights = fb.get_weight_logs(start_date_str)
        logger.info(f"Fetched {len(weights)} weight entries from Fitbit.")

        if not weights:
            logger.info("No data to sync.")
            return

        # Garmin
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        logger.info("Garmin login successful.")

        success_count = 0
        for entry in weights:
            try:
                weight_kg = entry.get('weight')
                date_str = entry.get('date') # YYYY-MM-DD
                time_str = entry.get('time') # HH:MM:SS
                fat = entry.get('fat') 
                
                dt_str = f"{date_str}T{time_str}"
                
                garmin.add_body_composition(
                    timestamp=dt_str,
                    weight=weight_kg,
                    percent_fat=fat
                )
                logger.info(f"Uploaded: {dt_str} - {weight_kg}kg")
                success_count += 1
            except Exception as e:
                logger.error(f"Failed to upload entry {entry}: {e}")

        logger.info(f"Sync complete. Successful: {success_count}/{len(weights)}")

    except Exception as e:
        logger.error(f"Fatal error during sync: {e}")

def job():
    sync_data()

if __name__ == "__main__":
    logger.info("Renpho(Fitbit)-Garmin Sync Service Started")
    
    # Run immediate sync
    logger.info("Running startup sync...")
    sync_data()
    
    # Schedule
    schedule.every().day.at("03:30").do(job) # 3:30 AM (giving time for Fitbit sync)
    logger.info("Scheduled daily sync at 03:30 AM")
    
    while True:
        schedule.run_pending()
        time.sleep(60)
