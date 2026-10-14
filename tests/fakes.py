"""Cliente falso do Claude para testar sem rede."""

from __future__ import annotations

import json
from types import SimpleNamespace


class FakeStream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


class FakeClaude:
    def __init__(self, clips=None, stop_reason="end_turn", raw_text=None, error=None):
        self.calls: list[dict] = []
        self._clips = clips or []
        self._stop_reason = stop_reason
        self._raw_text = raw_text
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        text = self._raw_text if self._raw_text is not None else json.dumps({"clips": self._clips})
        content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
        return FakeStream(SimpleNamespace(stop_reason=self._stop_reason, content=content))
