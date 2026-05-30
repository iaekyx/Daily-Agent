import asyncio
import websockets
import json

async def hello():
    async with websockets.connect("ws://localhost:8000/ws/chat") as ws:
        await ws.send(json.dumps({"type": "chat", "content": "hi"}))
        res = await ws.recv()
        print(res)

asyncio.run(hello())
