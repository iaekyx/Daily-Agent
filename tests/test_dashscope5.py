import os
import httpx
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    client = OpenAI(
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        http_client=httpx.Client(
            http1=True,
            http2=False,
            verify=False
        )
    )
    print("Sending request with http1=True...")
    res = client.chat.completions.create(
        model="qwen-max",
        messages=[{"role": "user", "content": "hi"}]
    )
    print("Success:", res.choices[0].message.content)
except Exception as e:
    import traceback
    traceback.print_exc()
