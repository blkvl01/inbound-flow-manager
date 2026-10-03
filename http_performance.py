"""Bounded HTTP compression and retry protection for local Dash operations."""

from collections import OrderedDict
from dataclasses import dataclass, field
import gzip
import hashlib
import threading
import time

from flask import Response, g, request


@dataclass
class _Action:
    digest: str
    started: float
    finished: threading.Event = field(default_factory=threading.Event)
    result: tuple | None = None


def install_http_performance(server):
    compressed = OrderedDict()
    compressed_bytes = 0
    actions = OrderedDict()
    mutex = threading.Lock()

    @server.before_request
    def protect_operation_retry():
        token = request.headers.get("X-Flow-Action", "")
        if request.method != "POST" or request.path != "/_dash-update-component" or not token:
            return None
        if len(token) > 128:
            return Response("Invalid operation token", status=400)
        payload = request.get_json(silent=True) or {}
        output = str(payload.get("output", ""))
        if "store-action.data" not in output and "kézi-refresh-store.data" not in output:
            return None
        digest = hashlib.sha256(request.get_data()).hexdigest()
        now = time.monotonic()
        with mutex:
            for old_token, old in list(actions.items()):
                if old.finished.is_set() and now - old.started > 300:
                    actions.pop(old_token, None)
            entry = actions.get(token)
            if entry is not None and entry.digest != digest:
                return Response("Operation token already used for another request", status=409)
            if entry is None:
                if len(actions) >= 128:
                    for old_token, old in list(actions.items()):
                        if old.finished.is_set():
                            actions.pop(old_token)
                            break
                    else:
                        return Response("Operations busy", status=503)
                entry = _Action(digest, now)
                actions[token] = entry
                g.flow_operation = entry
                return None
        # Never hold the mutex while the original storage operation is running.
        if not entry.finished.wait(20):
            return Response("Operation still running", status=409)
        if entry.result is None:
            return Response("Operation outcome unavailable", status=503)
        data, status, headers = entry.result
        return Response(data, status=status, headers=headers)

    @server.after_request
    def compress_response(response):
        nonlocal compressed_bytes
        is_asset = request.path.startswith("/assets/")
        if is_asset:
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
            response.headers.pop("Pragma", None)
            response.headers.pop("Expires", None)
        if response.status_code != 200 or request.method == "HEAD" or response.headers.get("Content-Encoding"):
            return response
        mime = response.mimetype or ""
        if not (mime.startswith("text/") or mime in {"application/json", "application/javascript", "image/svg+xml"}):
            return response
        response.vary.add("Accept-Encoding")
        if request.accept_encodings["gzip"] <= 0:
            return response
        key = (request.path, response.headers.get("ETag"), response.headers.get("Last-Modified")) if is_asset else None
        packed = None
        if key is not None and key[1]:
            with mutex:
                packed = compressed.get(key)
                if packed is not None:
                    compressed.move_to_end(key)
        if packed is None:
            response.direct_passthrough = False
            data = response.get_data()
            if len(data) < 1024:
                return response
            packed = gzip.compress(data, compresslevel=3, mtime=0)
            if len(packed) >= len(data):
                return response
            if key is not None and key[1] and len(packed) <= 8 * 1024 * 1024:
                with mutex:
                    previous = compressed.pop(key, None)
                    if previous is not None:
                        compressed_bytes -= len(previous)
                    compressed[key] = packed
                    compressed_bytes += len(packed)
                    while compressed_bytes > 8 * 1024 * 1024:
                        _, removed = compressed.popitem(last=False)
                        compressed_bytes -= len(removed)
        response.direct_passthrough = False
        original_body = response.response
        if hasattr(original_body, "close"):
            # A gzip cache hit does not iterate send_file's opened file at all.
            # Close that original iterator before replacing it with cached bytes.
            original_body.close()
        response.set_data(packed)
        response.headers["Content-Encoding"] = "gzip"
        etag, _ = response.get_etag()
        if etag:
            # The identity and gzip forms represent the same content, but have
            # different bytes. A weak validator permits conditional revalidation.
            response.set_etag(etag, weak=True)
        return response

    @server.after_request
    def remember_operation_result(response):
        entry = getattr(g, "flow_operation", None)
        if entry is not None:
            response.direct_passthrough = False
            entry.result = (response.get_data(), response.status_code, list(response.headers.items()))
            entry.finished.set()
        return response

    @server.teardown_request
    def release_failed_operation(error):
        entry = getattr(g, "flow_operation", None)
        if entry is not None and not entry.finished.is_set():
            entry.finished.set()
