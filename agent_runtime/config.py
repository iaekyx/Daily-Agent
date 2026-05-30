import os

import urllib3
from dotenv import load_dotenv
from openai import OpenAI

from .settings import WORKDIR

try:
    import httpx
except ImportError:
    httpx = None

load_dotenv(override=True)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def build_llm_client():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY")

    if gemini_key:
        print("[\033[92mLLM Config\033[0m] Using Gemini API...")
        kwargs = {}
        if httpx:
            kwargs["http_client"] = httpx.Client(verify=False, trust_env=True)
        client = OpenAI(
            api_key=gemini_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            **kwargs,
        )
        return client, os.environ.get("MODEL_ID", "gemini-2.5-flash")

    print("[\033[92mLLM Config\033[0m] Using DashScope API...")
    kwargs = {}
    if httpx:
        kwargs["http_client"] = httpx.Client(verify=False, trust_env=False)
    client = OpenAI(
        api_key=dashscope_key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        **kwargs,
    )
    return client, os.environ.get("MODEL_ID", "qwen-max")


client, MODEL = build_llm_client()
