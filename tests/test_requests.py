import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
api_key = os.environ.get("DASHSCOPE_API_KEY")

url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}
data = {
    "model": "qwen-max",
    "messages": [{"role": "user", "content": "hi"}]
}

try:
    print("Sending request using requests...")
    res = requests.post(url, headers=headers, json=data, verify=False)
    print("Status:", res.status_code)
    print("Response:", res.json())
except Exception as e:
    import traceback
    traceback.print_exc()
