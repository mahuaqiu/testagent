"""测试 WebSocket 连接到 Windows screen - JPEG 模式"""
import asyncio
import websockets
import json

async def test_ws():
    try:
        # 连接 Windows screen WebSocket with jpeg codec
        uri = "ws://localhost:8088/ws/screen/windows/windows_screen?monitor=1&codec=jpeg"
        print(f"Connecting to {uri}")
        async with websockets.connect(uri) as ws:
            print("WebSocket connected")
            # 接收几帧
            for i in range(3):
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    print(f"Received frame {i+1}: {len(data)} bytes")
                except asyncio.TimeoutError:
                    print(f"Timeout waiting for frame {i+1}")
                    break
            # 关闭
            await ws.close()
            print("WebSocket closed normally")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_ws())