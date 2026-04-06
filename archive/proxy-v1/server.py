"""Token Optimizer HTTP Proxy Server.

Transparent proxy that sits between applications and the Anthropic API.
Classifies requests, routes to optimal model tiers, manages caching,
and logs metrics.

Usage:
    python -m proxy.server
    # or
    from proxy.server import start
    start()
"""

import io
import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Dict, Optional, Tuple

from proxy import classifier, router, cache_manager, metrics, dashboard
from proxy.config import (
    PROXY_HOST, PROXY_PORT, ANTHROPIC_API_URL,
    DEFAULT_MAX_MODEL,
    HEADER_MAX_MODEL, HEADER_FORCE_MODEL,
    HEADER_RESP_MODEL, HEADER_RESP_TIER, HEADER_RESP_SAVINGS,
)

logger = logging.getLogger("token-optimizer.proxy")

# Headers to forward from client to Anthropic
FORWARD_HEADERS = {
    "x-api-key", "anthropic-version", "anthropic-beta",
    "content-type", "accept",
}


class ProxyHandler(BaseHTTPRequestHandler):
    """Handles incoming HTTP requests."""

    # Suppress default logging to stderr
    def log_message(self, format, *args):
        logger.debug(format, *args)

    # ── GET endpoints ──────────────────────────────────────────────

    def do_GET(self):
        if self.path == "/dashboard" or self.path == "/dashboard/":
            self._serve_dashboard()
        elif self.path == "/health":
            self._send_json(200, {"status": "ok", "port": PROXY_PORT})
        elif self.path == "/stats":
            try:
                stats = metrics.get_today_stats()
                self._send_json(200, stats)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
        else:
            self._send_json(404, {"error": "not found"})

    # ── POST /v1/messages ──────────────────────────────────────────

    def do_POST(self):
        if self.path != "/v1/messages":
            self._send_json(404, {"error": "not found"})
            return

        start_time = time.monotonic()

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return

        # Extract overrides
        force_model = self.headers.get(HEADER_FORCE_MODEL)
        max_model = self.headers.get(HEADER_MAX_MODEL) or DEFAULT_MAX_MODEL
        original_model = body.get("model")
        is_stream = body.get("stream", False)

        # Classify & route
        if force_model:
            tier = "FORCED"
            selected_model = force_model
            classification_details = {"forced": True}
            routing_decision = {"model": force_model, "tier": "FORCED",
                                "extended_thinking": False}
        else:
            tier, classification_details = classifier.classify(body)
            routing_decision = router.route(tier)
            selected_model = routing_decision["model"]

        # Apply model to request
        body["model"] = selected_model

        # Extended thinking for REASONING tier
        if routing_decision.get("extended_thinking"):
            # Only add if not already present
            if "thinking" not in body:
                body["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": min(body.get("max_tokens", 4096), 10000),
                }

        # Cache optimization
        cache_manager.optimize(body)

        # Forward to Anthropic
        try:
            if is_stream:
                self._handle_streaming(
                    body, start_time, original_model, selected_model,
                    tier, max_model, classification_details,
                )
            else:
                self._handle_sync(
                    body, start_time, original_model, selected_model,
                    tier, max_model, classification_details,
                )
        except Exception:
            # Fail-open: forward original request unchanged
            logger.exception("Proxy error — forwarding original request")
            try:
                original_body = json.loads(raw_body)
                self._forward_unchanged(
                    original_body, start_time, original_model, tier,
                    max_model, is_stream,
                )
            except Exception:
                logger.exception("Fail-open also failed")
                self._send_json(502, {
                    "type": "error",
                    "error": {"type": "proxy_error",
                              "message": "Token Optimizer proxy failed"},
                })

    # ── Sync request handling ──────────────────────────────────────

    def _handle_sync(self, body, start_time, original_model, selected_model,
                     tier, max_model, classification_details):
        """Forward a non-streaming request."""
        resp_body, resp_status, resp_headers = self._forward_to_anthropic(
            body, stream=False,
        )

        latency_ms = int((time.monotonic() - start_time) * 1000)

        # Parse usage from response
        try:
            resp_data = json.loads(resp_body)
            usage = resp_data.get("usage", {})
            tokens_in = usage.get("input_tokens", 0)
            tokens_out = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_creation = usage.get("cache_creation_input_tokens", 0)
        except (json.JSONDecodeError, AttributeError):
            tokens_in = tokens_out = cache_read = cache_creation = 0

        # Log metrics
        result = metrics.log_request(
            original_model=original_model,
            routed_model=selected_model,
            tier=tier,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            latency_ms=latency_ms,
            stream=False,
            classification_details=classification_details,
            baseline_model=max_model,
        )

        # Send response with optimizer headers
        self.send_response(resp_status)
        for key, val in resp_headers:
            if key.lower() not in ("transfer-encoding", "connection"):
                self.send_header(key, val)
        self.send_header(HEADER_RESP_MODEL, selected_model)
        self.send_header(HEADER_RESP_TIER, tier)
        self.send_header(HEADER_RESP_SAVINGS, f"${result['savings']:.6f}")
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body if isinstance(resp_body, bytes)
                         else resp_body.encode())

    # ── Streaming request handling ─────────────────────────────────

    def _handle_streaming(self, body, start_time, original_model,
                          selected_model, tier, max_model,
                          classification_details):
        """Forward a streaming (SSE) request."""
        body["stream"] = True

        req_data = json.dumps(body).encode()
        headers = self._build_anthropic_headers()
        headers["Accept"] = "text/event-stream"

        req = urllib.request.Request(
            f"{ANTHROPIC_API_URL}/v1/messages",
            data=req_data,
            headers=headers,
            method="POST",
        )

        ctx = ssl.create_default_context()

        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=300)
        except urllib.error.HTTPError as e:
            error_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header(HEADER_RESP_MODEL, selected_model)
            self.send_header(HEADER_RESP_TIER, tier)
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)
            metrics.log_request(
                original_model=original_model,
                routed_model=selected_model,
                tier=tier,
                tokens_in=0, tokens_out=0,
                stream=True,
                error=f"HTTP {e.code}",
                baseline_model=max_model,
            )
            return

        # Stream SSE to client
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header(HEADER_RESP_MODEL, selected_model)
        self.send_header(HEADER_RESP_TIER, tier)
        self.end_headers()

        tokens_in = tokens_out = cache_read = cache_creation = 0

        try:
            for line in resp:
                if isinstance(line, bytes):
                    decoded = line.decode("utf-8", errors="replace")
                else:
                    decoded = line

                self.wfile.write(line if isinstance(line, bytes)
                                 else line.encode())
                self.wfile.flush()

                # Parse SSE data for usage info
                if decoded.startswith("data: "):
                    try:
                        event_data = json.loads(decoded[6:].strip())
                        usage = event_data.get("usage", {})
                        if usage:
                            tokens_in = usage.get("input_tokens", tokens_in)
                            tokens_out = usage.get("output_tokens", tokens_out)
                            cache_read = usage.get(
                                "cache_read_input_tokens", cache_read)
                            cache_creation = usage.get(
                                "cache_creation_input_tokens", cache_creation)
                    except (json.JSONDecodeError, AttributeError):
                        pass
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("Client disconnected during stream")
        finally:
            resp.close()

        latency_ms = int((time.monotonic() - start_time) * 1000)

        result = metrics.log_request(
            original_model=original_model,
            routed_model=selected_model,
            tier=tier,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            latency_ms=latency_ms,
            stream=True,
            classification_details=classification_details,
            baseline_model=max_model,
        )

    # ── Fail-open fallback ─────────────────────────────────────────

    def _forward_unchanged(self, body, start_time, original_model, tier,
                           max_model, is_stream):
        """Forward original request unchanged when proxy logic fails."""
        if is_stream:
            body["stream"] = True
            req_data = json.dumps(body).encode()
            headers = self._build_anthropic_headers()
            headers["Accept"] = "text/event-stream"

            req = urllib.request.Request(
                f"{ANTHROPIC_API_URL}/v1/messages",
                data=req_data,
                headers=headers,
                method="POST",
            )
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, context=ctx, timeout=300)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            for line in resp:
                self.wfile.write(line)
                self.wfile.flush()
            resp.close()
        else:
            resp_body, resp_status, resp_headers = self._forward_to_anthropic(
                body, stream=False,
            )
            self.send_response(resp_status)
            for key, val in resp_headers:
                if key.lower() not in ("transfer-encoding", "connection"):
                    self.send_header(key, val)
            self.send_header("Content-Length", str(len(resp_body)))
            self.end_headers()
            self.wfile.write(resp_body if isinstance(resp_body, bytes)
                             else resp_body.encode())

        metrics.log_request(
            original_model=original_model,
            routed_model=original_model or "unknown",
            tier="FALLBACK",
            tokens_in=0, tokens_out=0,
            error="proxy_failopen",
            baseline_model=max_model,
        )

    # ── HTTP helpers ───────────────────────────────────────────────

    def _build_anthropic_headers(self) -> Dict[str, str]:
        """Build headers dict for the upstream Anthropic request."""
        headers = {"Content-Type": "application/json"}
        for h in FORWARD_HEADERS:
            val = self.headers.get(h)
            if val:
                headers[h] = val
        return headers

    def _forward_to_anthropic(self, body: Dict, stream: bool = False
                              ) -> Tuple[bytes, int, list]:
        """Send a request to Anthropic and return (body, status, headers)."""
        req_data = json.dumps(body).encode()
        headers = self._build_anthropic_headers()

        req = urllib.request.Request(
            f"{ANTHROPIC_API_URL}/v1/messages",
            data=req_data,
            headers=headers,
            method="POST",
        )

        ctx = ssl.create_default_context()

        try:
            resp = urllib.request.urlopen(req, context=ctx, timeout=300)
            resp_body = resp.read()
            resp_headers = resp.getheaders()
            return resp_body, resp.status, resp_headers
        except urllib.error.HTTPError as e:
            error_body = e.read()
            return error_body, e.code, e.headers.items()

    def _send_json(self, status: int, data: dict):
        """Send a JSON response."""
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self):
        """Serve the metrics dashboard."""
        try:
            stats = metrics.get_today_stats()
            html = dashboard.render(stats)
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            logger.exception("Dashboard render error")
            self._send_json(500, {"error": str(e)})


class ThreadedHTTPServer(HTTPServer):
    """HTTPServer that handles each request in a new thread."""

    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        t = Thread(target=self._handle_request_thread,
                   args=(request, client_address))
        t.daemon = True
        t.start()

    def _handle_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def start(host: str = PROXY_HOST, port: int = PROXY_PORT):
    """Start the proxy server (blocking)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    server = ThreadedHTTPServer((host, port), ProxyHandler)
    logger.info("Token Optimizer proxy listening on http://%s:%d", host, port)
    logger.info("Dashboard: http://%s:%d/dashboard", host, port)
    logger.info("Point ANTHROPIC_BASE_URL=http://%s:%d to use", host, port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down proxy")
        server.shutdown()


if __name__ == "__main__":
    start()
