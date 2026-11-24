"""Shared test tools: a fake Gemini + Cloudflare server on localhost, so the REAL `requests` code runs."""
from __future__ import annotations

import base64
import io
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from backend.config import Settings
from backend.models import (Character, Page, Story, StoryInput)


def png_bytes(size=(320, 240), color=(200, 120, 90)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def story_json(pages=5, title="The Brave Little Raincoat") -> dict:
    return {
        "title": title, "introduction": "Aarav loves rainy days.",
        "character": {"hair": "curly black hair", "face": "big brown eyes", "clothing": "a yellow raincoat",
                      "colors": "yellow and red", "distinctive_features": "red boots",
                      "companions": [{"name": "Momo", "description": "a small green parrot"}]},
        "pages": [{"text": f"Page {i} text.", "scene_description": f"Aarav on a sunny street, scene {i}"}
                  for i in range(1, pages + 1)],
        "moral": "Kindness makes every day brighter.",
    }


def gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def cf_text_reply(text: str) -> dict:
    return {"result": {"response": text}, "success": True, "errors": [], "messages": []}


def cf_image_reply(png: bytes | None = None) -> dict:
    return {"result": {"image": base64.b64encode(png or png_bytes()).decode()}, "success": True,
            "errors": [], "messages": []}


class FakeServer:
    """Scripted replies per route: 'gemini', 'models', 'cf_text', 'cf_image'. The last one repeats."""

    def __init__(self):
        self.script: dict[str, list] = {}
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _route(self) -> str:
                path = self.path.split("?")[0]
                if "/v1beta/models/" in path and path.endswith(":generateContent"):
                    return "gemini"
                if path.endswith("/v1beta/models"):
                    return "models"
                if "/ai/run/" in path:
                    return "cf_image" if "flux" in path else "cf_text"
                return "unknown"

            def _handle(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = None
                route = self._route()
                outer.requests.append({"route": route, "method": self.command, "path": self.path,
                                       "headers": dict(self.headers), "json": body})
                queue = outer.script.get(route) or [(500, {"error": {"message": "no script"}})]
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                status, payload = item[0], item[1]
                ctype = item[2] if len(item) > 2 else "application/json"
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_POST = do_GET = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def count(self, route: str) -> int:
        return sum(1 for r in self.requests if r["route"] == route)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries wait seconds in real life; never in tests."""
    waits: list[float] = []
    monkeypatch.setattr("backend.net.time.sleep", lambda s: waits.append(s))
    return waits


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()


@pytest.fixture
def settings(server, tmp_path) -> Settings:
    return Settings(
        gemini_api_key="g-secret-key", cloudflare_account_id="acct123", cloudflare_api_token="cf-secret-token",
        gemini_base_url=f"{server.url}/v1beta", cloudflare_base_url=f"{server.url}/client/v4",
        generated_dir=tmp_path / "generated", text_timeout=10, image_timeout=10,
    )


@pytest.fixture
def story_input() -> StoryInput:
    return StoryInput(child_name="Aarav", age=6)


@pytest.fixture
def story(story_input) -> Story:
    return Story(
        title="The Brave Little Raincoat", introduction="Intro.", moral="Be kind.",
        character=Character(name="Aarav", age=6, hair="curly black hair", face="big brown eyes",
                            clothing="a yellow raincoat", colors="yellow and red",
                            distinctive_features="red boots"),
        pages=[Page(page_number=i, text=f"Text {i}.", scene_description=f"Aarav in a sunny park, scene {i}")
               for i in range(1, 6)],
        input=story_input, text_model="test-model", image_seed=4242,
    )


def with_cf_only(s: Settings) -> Settings:
    return replace(s, gemini_api_key="")
