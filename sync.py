import os
import time
import json
import logging
import requests
import schedule
from datetime import datetime, timedelta
from garminconnect import Garmin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class RenphoClient:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        # Removed deprecated public key endpoint
        self.login_url = "https://renpho.qnclouds.com/api/v3/users/sign_in.json?app_id=Renpho"
        self.list_scale_user_url = "https://renpho.qnclouds.com/api/v3/scale_users/list_scale_user"
        self.measurements_url = "https://renpho.qnclouds.com/api/v2/measurements/list.json"
        
        self.session_key = None
        self.user_id = None
        self.scale_user_id = None

    def login(self):
        try:
            # Attempting login without RSA encryption first
            # If this fails, we might need to look for specific hashing
            payload = {
                "secure_flag": 1,
                "email": self.email,
                "password": self.password 
            }
            
            response = requests.post(self.login_url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            if 'terminal_user_session_key' in data:
                self.session_key = data['terminal_user_session_key']
                self.user_id = data['id']
                logger.info("Renpho login successful")
                
                # Fetch scale users to get the correct user ID for measurements
                self._get_scale_user()
            else:
                logger.error(f"Login failed: {data}")
                raise Exception("Renpho login failed")
                
        except Exception as e:
            logger.error(f"Error during Renpho login: {e}")
            raise

    def _get_scale_user(self):
        try:
            params = {
                "locale": "en",
                "terminal_user_session_key": self.session_key
            }
            response = requests.get(self.list_scale_user_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if 'scale_users' in data and len(data['scale_users']) > 0:
                # Typically the first user is the main user
                self.scale_user_id = data['scale_users'][0]['id']
                logger.info(f"Found scale user ID: {self.scale_user_id}")
            else:
                self.scale_user_id = self.user_id # Fallback
                logger.warning("No scale users found, falling back to main user ID")
                
        except Exception as e:
            logger.error(f"Error getting scale user: {e}")
            raise

    def get_measurements(self, start_timestamp):
        """
        Get measurements since start_timestamp (Unix timestamp)
        """
        try:
            measurements = []
            last_at = start_timestamp
            
            while True:
                params = {
                    "user_id": self.scale_user_id,
                    "last_at": last_at,
                    "locale": "en",
                    "app_id": "Renpho",
                    "terminal_user_session_key": self.session_key
                }
                
                response = requests.get(self.measurements_url, params=params)
                response.raise_for_status()
                data = response.json()
                
                if 'measurements' in data and data['measurements']:
                    batch = data['measurements']
                    measurements.extend(batch)
                    logger.info(f"Fetched {len(batch)} measurements...")
                    
                    new_last_at = batch[-1]['created_at']
                    if new_last_at <= last_at:
                        break 
                    last_at = new_last_at
                    
                    if len(batch) < 10:
                         break
                else:
                    break
            
            return measurements
            
        except Exception as e:
            logger.error(f"Error getting measurements: {e}")
            raise

def sync_data(backlog=False):
    logger.info("Starting synchronization process...")
    
    renpho_email = os.environ.get('RENPHO_EMAIL')
    renpho_password = os.environ.get('RENPHO_PASSWORD')
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    
    if not all([renpho_email, renpho_password, garmin_email, garmin_password]):
        logger.error("Missing environment variables. Please check your configuration.")
        return

    try:
        # Renpho Login
        renpho = RenphoClient(renpho_email, renpho_password)
        renpho.login()
        
        # Garmin Login
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        logger.info("Garmin login successful")
        
        # Determine start time
        if backlog:
            start_date = datetime.now() - timedelta(days=5*365)
            logger.info("Running backlog sync mode")
        else:
            # Sync from last week just to be safe and catch up
            start_date = datetime.now() - timedelta(days=7)
            logger.info("Running standard sync mode (last 7 days)")
            
        start_ts = int(start_date.timestamp())
        
        measurements = renpho.get_measurements(start_ts)
        logger.info(f"Found {len(measurements)} measurements to process")
        
        new_measurements_count = 0
        
        for m in measurements:
            weight_kg = m.get('weight')
            timestamp = m.get('created_at')
            date_obj = datetime.fromtimestamp(timestamp)
            
            percent_fat = m.get('bodyfat')
            percent_hydration = m.get('water')
            visceral_fat_mass = m.get('visceral_fat')
            bone_mass = m.get('bone')
            muscle_mass = m.get('muscle')
            
            try:
                garmin.add_body_composition(
                    timestamp=date_obj.isoformat(),
                    weight=weight_kg,
                    percent_fat=percent_fat,
                    percent_hydration=percent_hydration,
                    visceral_fat_mass=visceral_fat_mass,
                    bone_mass=bone_mass,
                    muscle_mass=muscle_mass
                )
                logger.info(f"Uploaded measurement: {date_obj} - {weight_kg}kg")
                new_measurements_count += 1
            except Exception as e:
                # Log but continue
                logger.error(f"Failed to upload measurement for {date_obj}: {e}")
        
        logger.info(f"Sync complete. Uploaded {new_measurements_count} measurements.")

    except Exception as e:
        logger.error(f"Fatal error during sync: {e}")

def job():
    sync_data(backlog=False)

if __name__ == "__main__":
    logger.info("Renpho-Garmin Sync Service Started")
    
    # 1. Run sync immediately on startup (as requested)
    logger.info("Running immediate startup sync...")
    
    # Check for backlog flag
    is_backlog = os.environ.get('RUN_BACKLOG', 'false').lower() == 'true'
    sync_data(backlog=is_backlog)
    
    # 2. Schedule daily job
    schedule.every().day.at("03:00").do(job)
    logger.info("Scheduled daily sync at 03:00 AM")
    
    while True:
        schedule.run_pending()
        time.sleep(60)
