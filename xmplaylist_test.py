import json
# pip install requests
import requests
from requests.models import Response

USER_HEADER = {
"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
"Accept-Language": "en-US,en;q=0.5",
"Connection": "keep-alive",
"Host": "xmplaylist.com",
"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0"
}

# Get the API version
# This code was last updated with 
# openapi version = 3.1.0
# info['version'] = 2.0.0
APIURL=f"https://xmplaylist.com/api/spec"
try:
    print("Send the request")
    api_info: Response = requests.get(url=APIURL, headers=USER_HEADER)
    print(f"status code: {api_info.status_code}")
    print("Here is the text")
    print(api_info.text)
    print("Here is the json")
    print(api_info.json())
    print("get the openapi version")
    print(openapi_ver = api_info.json()['openapi'])
    print("get the openapi version")
    print(info_ver = api_info.json()['info']['version'])
except Exception as e:
    print(f"There was an error: {e}")
    exit(1)
