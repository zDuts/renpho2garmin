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

    def get_latest_measurement(self):
        """
        Fetches the latest daily measurement.
        The endpoint 'dailyCalories' uses today's date to fetch status/data.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Fetching data for {today}...")
        
        data = self._api_call(ENDPOINT_DATA, {"data": today})
        
        # The data structure seems to contain 'fourElectrodeWeight' or 'eightElectrodeWeight'
        # inside the response.
        
        measurement = (
            data.get("fourElectrodeWeight") 
            or data.get("eightElectrodeWeight") 
            or {}
        )
        
        if not measurement:
            logger.warning("No weight data found in today's response.")
            # Fallback: Check if user_info has weight (last known)
            if self.user_info and self.user_info.get("weight"):
                 logger.info("Using last known weight from user profile.")
                 return {
                     "weight": self.user_info.get("weight"),
                     "timestamp": datetime.now().timestamp(), # Approximate
                     "bodyfat": None # Profile might not have this detailed
                 }
            return None

        # Extract fields
        # Note: API returns mixed camelCase keys
        return {
            "weight": measurement.get("weight"),
            "bodyfat": measurement.get("bodyfat"),
            "water": measurement.get("water"),
            "bone": measurement.get("bone"),
            "muscle": measurement.get("muscle"),
            "visfat": measurement.get("visfat"),
            "timestamp": measurement.get("localCreatedAt", datetime.now().timestamp())
        }

def sync_data(backlog=False):
    # Note: This Renpho Health API endpoint (dailyCalories) seems designed to return
    # the *current* state or latest measurement, not a full history list.
    # Backlog implementation might require finding a 'history' endpoint, but 
    # for now we will focus on sync-latest.
    
    logger.info("Renpho Health -> Garmin Sync Started")
    
    renpho_email = os.environ.get('RENPHO_EMAIL')
    renpho_password = os.environ.get('RENPHO_PASSWORD')
    garmin_email = os.environ.get('GARMIN_EMAIL')
    garmin_password = os.environ.get('GARMIN_PASSWORD')
    
    if not all([renpho_email, renpho_password, garmin_email, garmin_password]):
        logger.error("Missing credentials.")
        return

    try:
        # Renpho Health
        client = RenphoHealthClient(renpho_email, renpho_password)
        client.login()
        
        data = client.get_latest_measurement()
        if not data:
            logger.info("No data available to sync.")
            return
            
        weight = data.get('weight')
        logger.info(f"Latest Weight: {weight}kg")
        
        # Garmin Login
        garmin = Garmin(garmin_email, garmin_password)
        garmin.login()
        
        # timestamp from API is likely millis or seconds. 
        # localCreatedAt often is a timestamp int.
        ts = data.get('timestamp')
        # If ts is large int, maybe millis?
        now_ts = datetime.now().timestamp()
        
        # Defensive check for timestamp format
        if ts and ts > 4000000000: # millis
            ts = ts / 1000
            
        dt_str = datetime.fromtimestamp(ts).isoformat()
        
        garmin.add_body_composition(
            timestamp=dt_str,
            weight=weight,
            percent_fat=data.get('bodyfat'),
            percent_hydration=data.get('water'),
            visceral_fat_mass=data.get('visfat'),
            bone_mass=data.get('bone'),
            muscle_mass=data.get('muscle')
        )
        logger.info(f"Successfully uploaded to Garmin: {dt_str}")

    except Exception as e:
        logger.error(f"Sync failed: {e}")

def job():
    sync_data()

if __name__ == "__main__":
    logger.info("Service Started (Renpho Health API)")
    
    # Immediate Sync
    sync_data()
    
    schedule.every().day.at("03:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(60)
