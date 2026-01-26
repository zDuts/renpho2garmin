import fitbit
from fitbit.api import Fitbit
import os
import json
import webbrowser

def gather_keys():
    client_id = input("Enter Fitbit Client ID: ").strip()
    client_secret = input("Enter Fitbit Client Secret: ").strip()
    return client_id, client_secret

def persist_token(token_dict):
    if not os.path.exists('data'):
        os.makedirs('data')
    
    with open('data/fitbit_token.json', 'w') as f:
        json.dump(token_dict, f)
    print("Token saved to data/fitbit_token.json")

def main():
    print("--- Fitbit Initial Token Setup ---")
    client_id, client_secret = gather_keys()
    
    # The python-fitbit library usually requires a redirect_uri.
    # For personal apps, http://127.0.0.1:8080/ is common.
    redirect_uri = "http://127.0.0.1:8080/"
    
    oauth = fitbit.Fitbit(
        client_id,
        client_secret,
        redirect_uri=redirect_uri,
        timeout=10
    )
    
    # 1. Get the authorization URL
    auth_url, _ = oauth.client.authorize_token_url(redirect_uri=redirect_uri)
    
    print("\n1. Please visit this URL in your browser:")
    print(auth_url)
    print("\n2. Log in and allow access.")
    print("3. You will be redirected to a URL like: http://127.0.0.1:8080/?code=...state=...")
    
    code = input("\nPaste the full redirect URL (or just the 'code' part): ").strip()
    
    # Extract code if full URL is pasted
    if "code=" in code:
        import urllib.parse
        parsed = urllib.parse.urlparse(code)
        params = urllib.parse.parse_qs(parsed.query)
        code = params['code'][0]
    
    # 2. Exchange code for token
    try:
        token = oauth.client.fetch_access_token(code, redirect_uri=redirect_uri)
        print("\nSuccess! Got token:")
        print(token)
        persist_token(token)
    except Exception as e:
        print(f"\nError fetching token: {e}")

if __name__ == "__main__":
    main()
