import os
import httpx
import urllib3
from openai import OpenAI
from dotenv import load_dotenv
import traceback

# Suppress InsecureRequestWarning from verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv(override=True)
api_key = os.environ.get("GEMINI_API_KEY")
model_id = os.environ.get("MODEL_ID", "gemini-2.5-flash")

print("=== Gemini API Connection Test ===")
print("GEMINI_API_KEY:", f"{api_key[:8]}...{api_key[-8:]}" if api_key else "None")
print("MODEL_ID:", model_id)

try:
    client = OpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        # Gemini is blocked in China mainland, so we need proxy (trust_env=True).
        # We keep verify=False just in case of local TLS proxy verification issues.
        http_client=httpx.Client(verify=False, trust_env=True)
    )
    print("Sending chat completion request to Gemini...")
    res = client.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": "Hello Gemini! Please reply with a short sentence confirming you can hear me."}]
    )
    print("\n✅ Success! Gemini response:")
    print(res.choices[0].message.content)
except Exception as e:
    print("\n❌ Error connecting to Gemini:")
    traceback.print_exc()
    print("\n💡 Troubleshooting Tips:")
    print("1. Ensure your GEMINI_API_KEY is correct in your .env file.")
    print("2. Ensure your local proxy client (e.g., Clash) is running and configured to proxy international traffic.")
    print("3. Check if your system's HTTP_PROXY/HTTPS_PROXY environment variables are set correctly so httpx can route through it.")
