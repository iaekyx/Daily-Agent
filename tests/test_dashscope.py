import os
import httpx
from openai import OpenAI
from dotenv import load_dotenv
import traceback

load_dotenv(override=True)
api_key = os.environ.get("DASHSCOPE_API_KEY")
print("DASHSCOPE_API_KEY:", f"{api_key[:4]}...{api_key[-4:]}" if api_key else "None")

try:
    client = OpenAI(
        api_key=api_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        http_client=httpx.Client(verify=False)
    )
    print("Sending request...")
    res = client.chat.completions.create(
        model="qwen-max",
        messages=[{"role": "user", "content": "hi"}]
    )
    print("Success:", res.choices[0].message.content)
except Exception as e:
    print("Error:")
    traceback.print_exc()
