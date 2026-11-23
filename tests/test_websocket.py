import asyncio
import base64

from fastapi.testclient import TestClient

from app import server


class FakeLiveTranslateClient:
    received_audio = []

    def __init__(self, settings, options, on_event):
        self.options = options
        self.on_event = on_event
        self.events = asyncio.Queue()
        self.closed = False

    async def connect(self):
        return None

    async def send_audio(self, audio):
        type(self).received_audio.append(audio)
        await self.events.put(
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item-ru",
                "delta": "Привет",
            }
        )
        await self.events.put(
            {
                "type": "response.audio_transcript.delta",
                "response_id": "resp-zh",
                "delta": "你好",
            }
        )
        await self.events.put(
            {
                "type": "response.audio.delta",
                "response_id": "resp-zh",
                "delta": base64.b64encode(b"\x01\x00\x02\x00").decode(),
            }
        )

    async def finish(self):
        await self.events.put({"type": "session.finished"})

    async def receive(self):
        return await self.events.get()

    async def close(self):
        self.closed = True


def test_complete_local_websocket_flow(monkeypatch):
    FakeLiveTranslateClient.received_audio = []
    monkeypatch.setattr(server, "LiveTranslateClient", FakeLiveTranslateClient)
    monkeypatch.setattr(server.settings, "api_key", "test-key")

    client = TestClient(server.app)
    with client.websocket_connect("/ws/translate") as socket:
        socket.send_json(
            {
                "type": "config",
                "voice_clone_mode": "once",
                "phrases": {"стример": "主播"},
            }
        )
        ready = socket.receive_json()
        assert ready["type"] == "server_ready"
        assert ready["input_sample_rate"] == 16000
        assert ready["output_sample_rate"] == 24000

        socket.send_bytes(b"\x01\x00" * 1600)
        source = socket.receive_json()
        translation = socket.receive_json()
        audio = socket.receive_json()

        assert source["source_delta"] == "Привет"
        assert translation["translation_delta"] == "你好"
        assert audio["type"] == "model_audio"
        assert base64.b64decode(audio["audio"]) == b"\x01\x00\x02\x00"
        assert FakeLiveTranslateClient.received_audio == [b"\x01\x00" * 1600]

        socket.send_json({"type": "finish"})
        assert socket.receive_json()["type"] == "server_finished"


def test_invalid_client_config_is_reported(monkeypatch):
    monkeypatch.setattr(server.settings, "api_key", "test-key")
    client = TestClient(server.app)
    with client.websocket_connect("/ws/translate") as socket:
        socket.send_json({"type": "wrong"})
        response = socket.receive_json()
        assert response["type"] == "server_error"
        assert "type=config" in response["message"]
