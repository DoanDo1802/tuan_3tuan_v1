#!/usr/bin/env python3
import json
import os

import requests
import urllib3

from src.config_loader import load_dotenv

load_dotenv()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL = "https://atin-aitech.com:60443"
LOGIN_URL = f"{URL}/api/v1/user-management/auth/login"
CAMERAS_URL = f"{URL}/api/v1/cameras"

USERNAME = os.environ.get("WEB_USERNAME", "")
PASSWORD = os.environ.get("WEB_PASSWORD", "")

def main():
    session = requests.Session()
    session.verify = False
    
    # 1. Login
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "remember": True
    }
    
    print(f"Logging in to {LOGIN_URL}...")
    try:
        r = session.post(LOGIN_URL, json=payload, timeout=5)
        print(f"Login Response Status: {r.status_code}")
        try:
            res_data = r.json()
            print("Login Response Data (keys):", list(res_data.keys()))
        except Exception:
            res_data = {}
            print("Login Response Text:", r.text)
            
        if r.status_code not in [200, 201]:
            print("Login failed!")
            return
            
        # Extract access token if it's in the response
        token = res_data.get("access_token") or res_data.get("token") or res_data.get("accessToken")
        if token:
            print("Found access token, adding to headers...")
            session.headers.update({"Authorization": f"Bearer {token}"})
        else:
            print("No access token in JSON. Checking cookies...")
            print("Cookies in session:", session.cookies.get_dict())
            
        # 2. Get cameras
        # We saw in the JS bundle '/cameras?limit=100', '/cameras?limit=200', '/cameras?limit=500', '/cameras?limit=10000'
        # Let's try limit=200
        print(f"\nFetching cameras from {CAMERAS_URL}...")
        r_cam = session.get(CAMERAS_URL, params={"limit": 200}, timeout=5)
        print(f"Get Cameras Status: {r_cam.status_code}")
        
        if r_cam.status_code == 200:
            cam_data = r_cam.json()
            print("--- WEB CAMERA LIST RECEIVED ---")
            print(json.dumps(cam_data, indent=2, ensure_ascii=False))
            print("---------------------------------")
            
            # If the response is a list or has a cameras key
            cameras = []
            if isinstance(cam_data, list):
                cameras = cam_data
            elif isinstance(cam_data, dict):
                cameras = cam_data.get("cameras") or cam_data.get("data") or []
                
            print(f"Total cameras fetched from WEB: {len(cameras)}")
            for idx, cam in enumerate(cameras, 1):
                print(f"{idx}. Code: {cam.get('code')} | Name: {cam.get('name')} | Status: {cam.get('status')}")
        else:
            print("Failed to get cameras. Response content:")
            print(r_cam.text)
            
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == "__main__":
    main()
