"""A tiny stand-in for llama-server, used by tests. It loads no model and never touches a GPU.

It understands the flags QEDCode passes (-m, --host, --port, -c, -ngl, --parallel, ...), answers
/health and /v1/chat/completions (streaming), and can misbehave on purpose through env vars:

  FAKE_LLAMA_HELP_STYLE  value | bool | none   how --help describes --flash-attn
  FAKE_LLAMA_DELAY       seconds /health answers 503 ("loading") after start
  FAKE_LLAMA_DIE_WITH    print this to stderr and exit 1 straight away
  FAKE_LLAMA_ARGS_FILE   write the command-line arguments (one per line) to this file
  FAKE_LLAMA_REQUEST_FILE write the last chat request body to this file
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HELP_COMMON = """\
usage: llama-server [options]
  -m,    --model FILE              model path
         --host HOST               ip address to listen on
         --port PORT               port to listen on
  -c,    --ctx-size N              size of the prompt context
  -ngl,  --gpu-layers N            layers to store in VRAM
  -np,   --parallel N              number of slots
         --jinja                   use jinja template for chat
"""
HELP_VALUE = "  -fa,   --flash-attn [on|off|auto]  set Flash Attention use\n  -ctk,  --cache-type-k TYPE       KV cache type for K\n  -ctv,  --cache-type-v TYPE       KV cache type for V\n"
HELP_BOOL = "  -fa,   --flash-attn              enable Flash Attention\n  -ctk,  --cache-type-k TYPE       KV cache type for K\n"


def main(argv: list[str]) -> int:
    if "--help" in argv or "-h" in argv:
        style = os.environ.get("FAKE_LLAMA_HELP_STYLE", "value")
        sys.stdout.write(HELP_COMMON + {"value": HELP_VALUE, "bool": HELP_BOOL}.get(style, ""))
        return 0
    if "--version" in argv:
        sys.stderr.write("version: 0 (fake llama-server for tests)\n")
        return 0
    if os.environ.get("FAKE_LLAMA_DIE_WITH"):
        sys.stderr.write(os.environ["FAKE_LLAMA_DIE_WITH"] + "\n")
        return 1
    args_file = os.environ.get("FAKE_LLAMA_ARGS_FILE")
    if args_file:
        with open(args_file, "w", encoding="utf-8") as handle:
            handle.write("\n".join(argv))
    port = int(argv[argv.index("--port") + 1])
    ready_at = time.monotonic() + float(os.environ.get("FAKE_LLAMA_DELAY", "0"))
    request_file = os.environ.get("FAKE_LLAMA_REQUEST_FILE")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            return

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                if time.monotonic() < ready_at:
                    self._json(503, {"error": {"message": "Loading model"}})
                else:
                    self._json(200, {"status": "ok"})
            elif self.path.endswith("/models"):
                self._json(200, {"data": [{"id": "fake"}]})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            if request_file:
                with open(request_file, "w", encoding="utf-8") as handle:
                    json.dump(body, handle)
            last = body.get("messages", [{}])[-1].get("content", "")
            words = f"fake reply to: {last}".split(" ")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for index, word in enumerate(words):
                piece = word if index == 0 else " " + word
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
