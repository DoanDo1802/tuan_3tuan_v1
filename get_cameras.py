#!/usr/bin/env python3
import json
import os
import time
import sys
import paho.mqtt.client as mqtt

from src.config_loader import load_dotenv

load_dotenv()

MQTT_CONFIG = {
    "broker": os.environ.get("MQTT_BROKER", ""),
    "port": int(os.environ.get("MQTT_PORT", "1883")),
    "username": os.environ.get("MQTT_USERNAME", ""),
    "password": os.environ.get("MQTT_PASSWORD", ""),
    "topic": "smart_vms/cameras/company/21"
}

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"Connected to MQTT broker successfully. Subscribing to {MQTT_CONFIG['topic']}...")
        client.subscribe(MQTT_CONFIG["topic"], qos=1)
    else:
        print(f"Failed to connect, return code {rc}")
        sys.exit(1)

def on_message(client, userdata, msg):
    print(f"\nReceived message on topic: {msg.topic}")
    try:
        payload_str = msg.payload.decode("utf-8")
        data = json.loads(payload_str)
        print("--- CAMERA LIST JSON RECEIVED ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print("---------------------------------")
        
        cameras = data.get("cameras", [])
        print(f"Total cameras: {len(cameras)}")
        for idx, cam in enumerate(cameras, 1):
            print(f"{idx}. Name: {cam.get('name')} | Code: {cam.get('code')} | ID: {cam.get('id')} | Status: {cam.get('status')}")
            print(f"   AI Modules: {cam.get('ai_modules') or cam.get('aiModules')}")
            print(f"   Restream URLs: {cam.get('restream_urls') or cam.get('restreamUrls')}")
            print(f"   Zones count: {len(cam.get('zones', []))}")
            
    except Exception as e:
        print(f"Error parsing message: {e}")
        print("Raw payload:")
        print(msg.payload)
    
    # Exit after receiving the message
    client.disconnect()
    sys.exit(0)

def main():
    client = mqtt.Client(client_id=f"get_cameras_script_{int(time.time())}")
    client.username_pw_set(MQTT_CONFIG["username"], MQTT_CONFIG["password"])
    client.on_connect = on_connect
    client.on_message = on_message
    
    print(f"Connecting to broker {MQTT_CONFIG['broker']}:{MQTT_CONFIG['port']}...")
    try:
        client.connect(MQTT_CONFIG["broker"], MQTT_CONFIG["port"], 60)
    except Exception as e:
        print(f"Failed to connect to broker: {e}")
        sys.exit(1)
        
    # Start the loop
    client.loop_start()
    
    # Wait for up to 10 seconds for a message to arrive
    try:
        time.sleep(10)
        print("Timeout: No camera list message received within 10 seconds. Check if the topic is published or if you need to trigger a reload.")
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
