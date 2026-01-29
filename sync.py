import os
import time
import json
import logging
import base64
import requests
import schedule
import ssl
from datetime import datetime, timedelta
from garminconnect import Garmin
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Constants from hacs-renpho-health
API_BASE_URL = "https://cloud.renpho.com"
ENCRYPTION_KEY = "ed*wijdi$h6fe3ew"
APP_VERSION = "7.5.0"
ENDPOINT_LOGIN = "renpho-aggregation/user/login"
# The 'dailyCalories' endpoint seems to return the latest measurement payload in the repo logic
ENDPOINT_DATA = "RenphoHealth/healthManage/dailyCalories" 
DEVICE_TYPES = ["02D3", "02D5", "0B18", "0B38", "0B58", "0B78", "0BA6"]

class RenphoHealthClient:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.token = None
        self.user_id = None
        self.user_info = None

    def _aes_encrypt(self, plaintext):
        cipher = AES.new(ENCRYPTION_KEY.encode("utf-8"), AES.MODE_ECB)
        padded = pad(plaintext.encode("utf-8"), AES.block_size)
        return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")

    def _aes_decrypt(self, ciphertext):
        cipher = AES.new(ENCRYPTION_KEY.encode("utf-8"), AES.MODE_ECB)
        decrypted = cipher.decrypt(base64.b64decode(ciphertext))
        return unpad(decrypted, AES.block_size).decode("utf-8")

    def _get_headers(self):
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "language": "en",
            "appVersion": APP_VERSION,
            "platform": "android",
            "area": "US",
            "timeZone": "-6",
            "systemVersion": "16",
            "languageCode": "en",
            "userArea": "US",
        }
        if self.token:
            headers["token"] = self.token
        if self.user_id:
            headers["userId"] = str(self.user_id)
        return headers

    def _api_call(self, endpoint, data=None):
        url = f"{API_BASE_URL}/{endpoint}"
        
        # Encrypt request
        payload_str = json.dumps(data, separators=(",", ":")) if data else "{}"
        encrypted = self._aes_encrypt(payload_str)
        body = json.dumps({"encryptData": encrypted})
        
        try:
            response = requests.post(url, headers=self._get_headers(), data=body, timeout=30)
            response.raise_for_status()
            resp_json = response.json()
        except Exception as e:
            logger.error(f"API Request Failed: {e}")
            raise

        code = resp_json.get("code")
        if code != 101: # 101 seems to be success code in this API
             msg = resp_json.get("msg", "Unknown error")
             logger.error(f"API Error {code}: {msg}")
             raise Exception(f"Renpho API authentication/request failed: {msg}")

        # Decrypt response
        if resp_json.get("data"):
            try:
                decrypted = self._aes_decrypt(resp_json["data"])
                # logger.info(f"Decrypted Response: {decrypted}") 
                return json.loads(decrypted)
            except Exception as e:
                logger.error("Failed to decrypt response")
                raise
        return {}

    def login(self):
        logger.info("Attempting login to Renpho Health API...")
        login_data = {
            "questionnaire": {},
            "login": {
                "email": self.email,
                "password": self.password,
                "areaCode": "US",
                "appRevision": APP_VERSION,
                "cellphoneType": "HomeAssistant",
                "systemType": "11",
                "platform": "android",
            },
            "bindingList": {"deviceTypes": DEVICE_TYPES},
        }
        
        decrypted_data = self._api_call(ENDPOINT_LOGIN, login_data)
        
        if "login" not in decrypted_data:
             raise Exception("Login response missing 'login' data")

        self.token = decrypted_data["login"].get("token")
        self.user_id = decrypted_data["login"].get("id")
        self.user_info = decrypted_data["login"]
        
        logger.info(f"Login successful. User ID: {self.user_id}")

    def get_measurement(self, date_obj=None):
        """
        Fetches the daily measurement for a specific date.
        """
        if date_obj is None:
            date_obj = datetime.now()
            
        date_str = date_obj.strftime("%Y-%m-%d")
        logger.info(f"Fetching data for {date_str}...")
        
        data = self._api_call(ENDPOINT_DATA, {"data": date_str})
        
        measurement = (
            data.get("fourElectrodeWeight") 
            or data.get("eightElectrodeWeight") 
            or {}
        )
        
        if not measurement:
            # Only warn if checking today, otherwise silent for backlog holes
            if date_obj.date() == datetime.now().date():
                 logger.warning(f"No weight data found for {date_str}.")
            return None

        # Extract fields
        return {
            "weight": measurement.get("weight"),
            "bodyfat": measurement.get("bodyfat"),
            "water": measurement.get("water"),
            "bone": measurement.get("bone"),
            "muscle": measurement.get("muscle"),
            "visfat": measurement.get("visfat"),
            "timestamp": measurement.get("localCreatedAt", datetime.now().timestamp())
        }

def process_day(client, garmin, date_obj):
    data = client.get_measurement(date_obj)
    if not data:
        return False
        
    weight = data.get('weight')
    
    # timestamp from API is likely millis or seconds. 
    ts = data.get('timestamp')
    
    # Ensure ts is a number (it might come as a string from API)
    try:
        ts = float(ts)
    except (ValueError, TypeError):
        # Try parsing "YYYY-MM-DD HH:MM:SS"
        try:
            dt_obj = datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
            ts = dt_obj.timestamp()
        except ValueError:
            ts = date_obj.timestamp() # Fallback to query date

    # If ts is large int (millis), convert to seconds
    if ts > 4000000000: 
        ts = ts / 1000
        
    # Correction for Renpho server timezone bug (returns +8h or CST aligned timestamps)
    # User requested to ALWAYS deduct 8 hours.
    logger.info(f"Applying unconditional -8h correction to timestamp {ts} (Renpho Server Timezone Fix).")
    ts -= 8 * 3600

    dt_obj = datetime.fromtimestamp(ts)
    dt_str = dt_obj.isoformat()
    
    # Validation: If we requested a specific date_obj (backlog), 
    # verify the returned timestamp matches that day.
    # Allow 24h buffer for timezone differences, but if it matches 
    # "today" when we asked for "last year", it's a fallback response to ignore.
    if date_obj.date() != datetime.now().date(): # If we are NOT asking for today
        data_date = dt_obj.date()
        requested_date = date_obj.date()
        
        # If the data date is significantly different (e.g. more than 1 day off)
        if abs((data_date - requested_date).days) > 1:
            logger.info(f"Skipping: Requested {requested_date}, but API returned data for {data_date} (likely latest fallback).")
            return False
    
    try:
        garmin.add_body_composition(
            timestamp=dt_str,
            weight=weight,
            percent_fat=data.get('bodyfat'),
            percent_hydration=data.get('water'),
            visceral_fat_mass=data.get('visfat'),
            bone_mass=data.get('bone'),
            muscle_mass=data.get('muscle')
        )
        logger.info(f"Uploaded: {dt_str} - {weight}kg")
        return True
    except Exception as e:
        logger.error(f"Upload failed for {dt_str}: {e}")
        return False

def sync_data(backlog=False):
    logger.info("Renpho Health -> Garmin Sync Started")
    
    renpho_email = os.environ.get('RENPHO_EMAIL')
    renpho_password = os.environ.get('RENPHO_PASSWORD')
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    
    if not all([renpho_email, renpho_password, garmin_email, garmin_password]):
        logger.error("Missing credentials.")
        return

    try:
        # Renpho Health Login
        client = RenphoHealthClient(renpho_email, renpho_password)
        client.login()
        
        # Garmin Login
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        
        # NOTE: The Renpho Health 'dailyCalories' endpoint only returns the LATEST measurement.
        # It does NOT support fetching specific historical dates.
        # Therefore, true 'backlog' syncing is impossible with this specific endpoint.
        # We will strictly sync the LATEST available measurement if it matches today's date.
        
        logger.info("Fetching latest measurement from Renpho...")
        # We pass today's date just to satisfy the payload requirement
        today = datetime.now()
        process_day(client, garmin, today)

    except Exception as e:
        logger.error(f"Sync failed: {e}")

def job():
    sync_data(backlog=False)

if __name__ == "__main__":
    logger.info("Service Started (Renpho Health API)")
    
    # Run immediate sync on startup
    sync_data()
    
    sync_time = os.environ.get('SYNC_TIME', '03:00')
    schedule.every().day.at(sync_time).do(job)
    logger.info(f"Scheduled daily sync at {sync_time}")
    
    while True:
        schedule.run_pending()
        time.sleep(60)
