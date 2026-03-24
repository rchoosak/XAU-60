"""
HTTP bridge between the Python bot and a MetaTrader 5 EA.

The Python process hosts a tiny local HTTP server. The EA polls that server
for work, executes the command inside MetaTrader 5, then posts the result
back. This lets non-Windows environments drive a real MT5 terminal running
on Windows without importing the MetaTrader5 Python package locally.
"""
from __future__ import annotations

import json
import os
import queue
import socketserver
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


# MT5 constants mirrored from the official Python package.
TIMEFRAME_M1 = 1
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 60
TIMEFRAME_H4 = 240
TIMEFRAME_D1 = 1440
TIMEFRAME_W1 = 10080
TIMEFRAME_MN1 = 43200

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5

TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
TRADE_ACTION_CLOSE_BY = 10

ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2
ORDER_TIME_SPECIFIED_DAY = 3

ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2

TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_ERROR = 10011

DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1


def _to_epoch(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    raise TypeError(f"Unsupported datetime value: {value!r}")


def _to_namespace(payload: Optional[Dict[str, Any]]) -> Optional[SimpleNamespace]:
    if payload is None:
        return None
    return SimpleNamespace(**payload)


def _to_namespaces(items: Optional[List[Dict[str, Any]]]) -> List[SimpleNamespace]:
    if not items:
        return []
    return [SimpleNamespace(**item) for item in items]


@dataclass
class _PendingRequest:
    request_id: str
    action: str
    params: Dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[Dict[str, Any]] = None


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _BridgeState:
    def __init__(self, token: str):
        self.token = token
        self.pending: "queue.Queue[_PendingRequest]" = queue.Queue()
        self.waiting: Dict[str, _PendingRequest] = {}
        self.lock = threading.Lock()

    def enqueue(self, action: str, params: Dict[str, Any]) -> _PendingRequest:
        request = _PendingRequest(
            request_id=str(uuid.uuid4()),
            action=action,
            params=params,
        )
        with self.lock:
            self.waiting[request.request_id] = request
        self.pending.put(request)
        return request

    def pop_for_worker(self, timeout_s: float) -> Optional[_PendingRequest]:
        try:
            return self.pending.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def complete(self, response: Dict[str, Any]) -> bool:
        request_id = response.get("request_id", "")
        with self.lock:
            request = self.waiting.get(request_id)
        if request is None:
            return False

        request.response = response
        request.event.set()
        return True

    def release(self, request_id: str) -> None:
        with self.lock:
            self.waiting.pop(request_id, None)


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "MT5Bridge/1.0"

    @property
    def state(self) -> _BridgeState:
        return self.server.state  # type: ignore[attr-defined]

    def _send_text(self, status: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, query: Dict[str, List[str]]) -> bool:
        provided = self.headers.get("X-MT5-Token") or query.get("token", [""])[0]
        return provided == self.state.token

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json(200, {"ok": True})
            return

        if not self._authorized(query):
            self._send_json(403, {"ok": False, "error": "unauthorized"})
            return

        if parsed.path != "/poll":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        timeout_ms = int(query.get("timeout_ms", ["25000"])[0] or "25000")
        request = self.state.pop_for_worker(max(timeout_ms / 1000.0, 0.1))
        if request is None:
            self._send_text(200, "status=empty\n")
            return

        lines = [
            "status=ok",
            f"request_id={request.request_id}",
            f"action={request.action}",
        ]
        for key, value in request.params.items():
            if value is None:
                continue
            lines.append(f"param_{key}={value}")
        self._send_text(200, "\n".join(lines) + "\n")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if not self._authorized(query):
            self._send_json(403, {"ok": False, "error": "unauthorized"})
            return

        if parsed.path != "/response":
            self._send_json(404, {"ok": False, "error": "not_found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        if not self.state.complete(payload):
            self._send_json(404, {"ok": False, "error": "unknown_request"})
            return

        self._send_json(200, {"ok": True})

    def log_message(self, format: str, *args: Any) -> None:
        return


class MT5BridgeServer:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        self.state = _BridgeState(token)
        self.httpd: Optional[_ThreadedHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.httpd is not None:
            return

        self.httpd = _ThreadedHTTPServer((self.host, self.port), _BridgeRequestHandler)
        self.httpd.state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None

    def roundtrip(self, action: str, params: Dict[str, Any], timeout_s: float) -> Dict[str, Any]:
        request = self.state.enqueue(action, params)
        completed = request.event.wait(timeout=max(timeout_s, 0.1))
        try:
            if not completed or request.response is None:
                raise TimeoutError(
                    f"Timed out waiting for MT5 EA response for action '{action}'. "
                    f"Check that mt5_server.mq5 is attached and polling {self.host}:{self.port}."
                )
            return request.response
        finally:
            self.state.release(request.request_id)


class MT5Client:
    """
    Drop-in MT5-like client backed by the EA bridge.
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        token: Optional[str] = None,
        request_timeout: Optional[int] = None,
        poll_timeout: Optional[int] = None,
    ):
        self.host = host or os.getenv("MT5_BRIDGE_HOST", "0.0.0.0")
        self.port = int(port or os.getenv("MT5_BRIDGE_PORT", "8765"))
        self.token = token or os.getenv("MT5_BRIDGE_TOKEN", "change-me")
        self.request_timeout = int(request_timeout or os.getenv("MT5_BRIDGE_TIMEOUT", "30000"))
        self.poll_timeout = int(poll_timeout or os.getenv("MT5_BRIDGE_POLL_TIMEOUT", "25000"))
        self.bridge = MT5BridgeServer(self.host, self.port, self.token)
        self._initialized = False
        self._last_error: Tuple[int, str] = (0, "")

    def _set_error(self, code: int, message: str) -> None:
        self._last_error = (code, message)

    def _call(self, action: str, **params: Any) -> Dict[str, Any]:
        try:
            response = self.bridge.roundtrip(
                action=action,
                params=params,
                timeout_s=self.request_timeout / 1000.0,
            )
        except TimeoutError as exc:
            self._set_error(-10000, str(exc))
            return {"ok": False, "error": str(exc), "result": None}

        if not response.get("ok", False):
            message = response.get("error", "unknown_error")
            self._set_error(int(response.get("error_code", -1)), message)
        else:
            self._set_error(0, "")
        return response

    def initialize(self, **_: Any) -> bool:
        try:
            self.bridge.start()
            self._initialized = True
            return True
        except OSError as exc:
            self._set_error(exc.errno or -1, str(exc))
            return False

    def login(self, *_: Any, **__: Any) -> bool:
        return True

    def shutdown(self) -> None:
        self.bridge.stop()
        self._initialized = False

    def last_error(self) -> Tuple[int, str]:
        return self._last_error

    def terminal_info(self) -> Optional[SimpleNamespace]:
        response = self._call("terminal_info")
        return _to_namespace(response.get("result"))

    def account_info(self) -> Optional[SimpleNamespace]:
        response = self._call("account_info")
        return _to_namespace(response.get("result"))

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        response = self._call("symbol_select", symbol=symbol, enable=int(enable))
        return bool(response.get("ok"))

    def symbol_info(self, symbol: str) -> Optional[SimpleNamespace]:
        response = self._call("symbol_info", symbol=symbol)
        return _to_namespace(response.get("result"))

    def symbol_info_tick(self, symbol: str) -> Optional[SimpleNamespace]:
        response = self._call("symbol_info_tick", symbol=symbol)
        return _to_namespace(response.get("result"))

    def copy_rates_from(self, symbol: str, timeframe: int, date_from: Any, count: int) -> Optional[List[Dict[str, Any]]]:
        response = self._call(
            "copy_rates_from",
            symbol=symbol,
            timeframe=timeframe,
            start_time=_to_epoch(date_from),
            count=count,
        )
        return response.get("result")

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start_pos: int, count: int) -> Optional[List[Dict[str, Any]]]:
        response = self._call(
            "copy_rates_from_pos",
            symbol=symbol,
            timeframe=timeframe,
            start_pos=start_pos,
            count=count,
        )
        return response.get("result")

    def copy_rates_range(self, symbol: str, timeframe: int, date_from: Any, date_to: Any) -> Optional[List[Dict[str, Any]]]:
        response = self._call(
            "copy_rates_range",
            symbol=symbol,
            timeframe=timeframe,
            start_time=_to_epoch(date_from),
            end_time=_to_epoch(date_to),
        )
        return response.get("result")

    def order_send(self, request: Dict[str, Any]) -> Optional[SimpleNamespace]:
        response = self._call("order_send", **request)
        return _to_namespace(response.get("result"))

    def positions_get(
        self,
        symbol: Optional[str] = None,
        ticket: Optional[int] = None,
    ) -> Optional[List[SimpleNamespace]]:
        response = self._call("positions_get", symbol=symbol or "", ticket=ticket or 0)
        result = response.get("result")
        if result is None:
            return None
        return _to_namespaces(result)

    def history_deals_get(self, date_from: Any, date_to: Any) -> Optional[List[SimpleNamespace]]:
        response = self._call(
            "history_deals_get",
            start_time=_to_epoch(date_from),
            end_time=_to_epoch(date_to),
        )
        result = response.get("result")
        if result is None:
            return None
        return _to_namespaces(result)
