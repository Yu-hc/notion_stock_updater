import base64
import requests
import os
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

CLIENT_ID = os.getenv('CLIENT_ID')  # Your Notion integration's client ID
CLIENT_SECRET = os.getenv('CLIENT_SECRET')  # Your Notion integration's client secret
REDIRECT_URI = os.getenv('REDIRECT_URI')  # The redirect URI you configured
CODE = os.getenv('CODE')  # The temporary code from the authorization URL

url = "https://api.notion.com/v1/oauth/token"

# Notion requires Basic Auth header: base64(client_id:client_secret)
auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

headers = {
    "Authorization": f"Basic {auth_header}",
    "Content-Type": "application/json",
}

payload = {
    "grant_type": "authorization_code",
    "code": CODE,
    "redirect_uri": REDIRECT_URI,
}

response = requests.post(url, json=payload, headers=headers)
data = response.json()

if response.status_code == 200:
    print("Your Access Token:")
    print(data["access_token"])
else:
    print(f"Error: {data}")