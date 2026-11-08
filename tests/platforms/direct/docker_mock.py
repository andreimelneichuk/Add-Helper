"""Локальный HTTP-мок Direct API для проверки адаптера в Docker.

Отвечает только на Dictionaries.getGeoRegions и использует фиктивный регион.
Этот сервер не является песочницей Яндекса и не должен использоваться для запуска рекламы.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

FAKE_REGION_ID = 999_999


class DirectMockHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if urlparse(self.path).path != "/healthz":
            self.send_error(404)
            return
        self._json(200, {"status": "ok", "mode": "fixture-only"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/json/v5/dictionaries":
            self.send_error(404)
            return
        try:
            request: dict[str, Any] = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        params = request.get("params", {})
        exact_names = params.get("SelectionCriteria", {}).get("ExactNames", [])
        if request.get("method") != "getGeoRegions" or exact_names != ["Тольятти"]:
            self._json(
                200,
                {
                    "error": {
                        "error_code": 4000,
                        "error_string": "Invalid test request",
                        "error_detail": "Expected exact-name lookup for Тольятти",
                    }
                },
            )
            return

        self._json(
            200,
            {
                "result": {
                    "GeoRegions": [
                        {
                            "GeoRegionId": FAKE_REGION_ID,
                            "GeoRegionName": "Тольятти",
                            "ParentGeoRegionNames": {"Items": ["Самарская область", "Россия"]},
                        }
                    ]
                }
            },
            units="1/999/1000",
        )

    def log_message(self, format: str, *args: object) -> None:
        # Не выводим заголовки и токены из HTTP-запроса.
        logging.getLogger("direct-mock").info("%s", format % args)

    def _json(self, status: int, payload: dict[str, Any], units: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("RequestId", "local-direct-mock")
        if units is not None:
            self.send_header("Units", units)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("direct-mock").info("fixture mock listening on 0.0.0.0:8765")
    ThreadingHTTPServer(("0.0.0.0", 8765), DirectMockHandler).serve_forever()
