import os
import time
import json
import logging
import requests
import schedule
from datetime import datetime, timedelta
from garminconnect import Garmin
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64

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
        self.public_key_url = "https://renpho.qnclouds.com/api/v3/users/get_ras_public_key?app_id=Renpho"
        self.login_url = "https://renpho.qnclouds.com/api/v3/users/sign_in.json?app_id=Renpho"
        self.list_scale_user_url = "https://renpho.qnclouds.com/api/v3/scale_users/list_scale_user"
        self.measurements_url = "https://renpho.qnclouds.com/api/v2/measurements/list.json"
        
        self.session_key = None
        self.user_id = None
        self.scale_user_id = None

    def _get_public_key(self):
        try:
            response = requests.get(self.public_key_url)
            response.raise_for_status()
            return response.json()['public_key']
        except Exception as e:
            logger.error(f"Failed to get public key: {e}")
            raise

    def _encrypt_password(self, public_key_str):
        # Format the public key string to proper PEM format if needed
        if not public_key_str.startswith('-----BEGIN PUBLIC KEY-----'):
            public_key_str = f"-----BEGIN PUBLIC KEY-----\n{public_key_str}\n-----END PUBLIC KEY-----"
            
        key = RSA.importKey(public_key_str)
        cipher = PKCS1_v1_5.new(key)
        encrypted = cipher.encrypt(self.password.encode('utf-8'))
        return base64.b64encode(encrypted).decode('utf-8')

    def login(self):
        try:
            public_key = self._get_public_key()
            encrypted_password = self._encrypt_password(public_key)
            
            payload = {
                "secure_flag": 1,
                "email": self.email,
                "password": encrypted_password
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
                raise Exception("No scale users found")
                
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
                    
                    # Update last_at to the timestamp of the last item in batch
                    # The API returns items in chronological order usually? 
                    # Assuming we need to page through. If the batch is small, we might be done.
                    # Renpho API behavior needs to be handled carefully. Usually last_at implies 'since'.
                    
                    # Let's ensure progress.
                    new_last_at = batch[-1]['created_at']
                    if new_last_at <= last_at:
                        break # Avoid infinite loop if no new data
                    last_at = new_last_at
                    
                    if len(batch) < 10: # Assuming page size is likely larger than 10
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
        # Using a fixed session file path to persist session if possible
        # but in Docker it might reset unless volume mounted.
        # For now, re-login every time or rely on library internal handling.
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        logger.info("Garmin login successful")
        
        # Determine start time
        if backlog:
            # Sync from a long time ago, e.g., 5 years
            start_date = datetime.now() - timedelta(days=5*365)
            logger.info("Running backlog sync mode")
        else:
            # Sync from yesterday
            start_date = datetime.now() - timedelta(days=2)
            logger.info("Running daily sync mode")
            
        start_ts = int(start_date.timestamp())
        
        measurements = renpho.get_measurements(start_ts)
        logger.info(f"Found {len(measurements)} measurements to process")
        
        new_measurements_count = 0
        
        for m in measurements:
            # Process each measurement
            # Renpho weight is usually in kg. Check 'weight' field.
            weight_kg = m.get('weight')
            timestamp = m.get('created_at')
            date_obj = datetime.fromtimestamp(timestamp)
            
            # Additional metrics if available
            percent_fat = m.get('bodyfat')
            percent_hydration = m.get('water')
            visceral_fat_mass = m.get('visceral_fat')
            bone_mass = m.get('bone')
            muscle_mass = m.get('muscle')
            
            # Garmin expects weight in kg (or unit system dependent? API usually takes SI)
            # garminconnect library: add_body_composition(timestamp, weight, percent_fat=None, percent_hydration=None, visceral_fat_mass=None, bone_mass=None, muscle_mass=None, metabolic_age=None, physique_rating=None, visceral_fat_rating=None, bmi=None)
            
            try:
                # Add to Garmin
                # Note: user might want to avoid duplicates. Garmin usually overwrites or ignores if same timestamp.
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
                logger.error(f"Failed to upload measurement for {date_obj}: {e}")
        
        logger.info(f"Sync complete. Uploaded {new_measurements_count} measurements.")

    except Exception as e:
        logger.error(f"Fatal error during sync: {e}")

def job():
    sync_data(backlog=False)

if __name__ == "__main__":
    logger.info("Renpho-Garmin Sync Service Started")
    
    # Check if we should run backlog on startup
    if os.environ.get('RUN_BACKLOG', 'false').lower() == 'true':
        logger.info("Backlog run requested via environment variable")
        sync_data(backlog=True)
    
    # Schedule daily job at 3:00 AM
    schedule.every().day.at("03:00").do(job)
    logger.info("Scheduled daily sync at 03:00 AM")
    
    while True:
        schedule.run_pending()
        time.sleep(60)
