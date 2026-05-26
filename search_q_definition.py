#!/usr/bin/env python3
import requests
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

JS_URL = "https://atin-aitech.com:60443/assets/index-iiyBg6gv.js"

print("Downloading JS bundle...")
r = requests.get(JS_URL, verify=False)
text = r.text

print("Searching for the definition of q_ function...")
# Let's search for "q_(" or "const q_ =" or "function q_"
# We found q_("POST", ... or similar.
# Let's search for "q_=" or "q_ = " or "q_(" or "function q_"
pos = text.find("q_ = ")
if pos == -1:
    pos = text.find("q_(")
if pos == -1:
    pos = text.find("function q_")

if pos != -1:
    start = max(0, pos - 1000)
    end = min(len(text), pos + 1000)
    print("Found around pos:", pos)
    print(text[start:end])
else:
    print("Could not find exact q_ definition, doing substring search for 'q_'")
    # Let's print occurrences of "q_" that look like a function definition
    for match in re.finditer(r'q_\b', text):
        start = max(0, match.start() - 200)
        end = min(len(text), match.end() + 200)
        print(f"\n--- Occurrence of q_ at {match.start()} ---")
        print(text[start:end])
