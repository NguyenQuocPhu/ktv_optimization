#!/usr/bin/env python3
"""Server review map cho lõi routing: dán message IN (JSON) -> chạy `ktv_core plan` -> trả OUT cho trang HTML.

Chạy:
  python3 tools/review_map/server.py [--port 8090] [--osrm http://127.0.0.1:5000] [--binary core/build/ktv_core]
  rồi mở http://127.0.0.1:8090

Chỉ dùng thư viện chuẩn + binary có sẵn trong repo (không thêm dependency).
"""
import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent

SETTINGS = {
    "binary": str(REPO / "core" / "build" / "ktv_core"),
    "osrm": "",  # trống = chim bay; UI có checkbox "dùng OSRM" (chỉ khi server có --osrm)
    "revision": "",
}


def git_revision():
    """Commit đang build binary (để UI ghi nguồn OUT), không có git thì trả rỗng."""
    try:
        done = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5)
        return done.stdout.strip() if done.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""

# Hình học đường bộ cho map: gọi OSRM /route mỗi chặng, cache theo cặp tọa độ.
# OSRM public giới hạn ~1 req/s → giãn nhịp khi upstream là project-osrm.org.
ROAD_CACHE: dict = {}
ROAD_LOCK = threading.Lock()
ROAD_LAST = [0.0]


def road_points(start, end):
    """start/end = (lat, lng) → danh sách [lat, lng] theo đường bộ, hoặc None."""
    key = f"{start};{end}"
    if key in ROAD_CACHE:
        return ROAD_CACHE[key]
    if not SETTINGS["osrm"]:
        return None
    url = (f"{SETTINGS['osrm']}/route/v1/driving/{start[1]},{start[0]};{end[1]},{end[0]}"
           "?overview=full&geometries=geojson&steps=false")
    interval = 1.05 if "project-osrm.org" in SETTINGS["osrm"] else 0.05
    with ROAD_LOCK:
        wait = interval - (time.monotonic() - ROAD_LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = json.load(response)
            route = (body.get("routes") or [{}])[0]
            points = [[lat, lng] for lng, lat in route["geometry"]["coordinates"]]
        except Exception:  # noqa: BLE001 — lỗi mạng/route → map giữ đường thẳng
            points = None
        ROAD_LAST[0] = time.monotonic()
    ROAD_CACHE[key] = points
    return points


def run_plan(text: str, use_osrm: bool, use_explain: bool = True):
    """Chạy ktv_core plan trên nội dung JSON. Trả dict cho UI."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "in.json"
        out_path = Path(tmp) / "out.jsonl"
        in_path.write_text(text, encoding="utf-8")
        command = [SETTINGS["binary"], "plan", str(in_path), "--out", str(out_path)]
        if use_osrm and SETTINGS["osrm"]:
            command += ["--osrm", SETTINGS["osrm"]]
        if use_explain:
            command.append("--explain")
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "ktv_core chạy quá 120 giây"}
        out = None
        if out_path.exists():
            lines = [l for l in out_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            if lines:
                try:
                    out = json.loads(lines[0])
                except json.JSONDecodeError as error:
                    return {"ok": False, "error": f"OUT không phải JSON: {error}"}
    return {
        "ok": True,
        "out": out,
        "stdout": done.stdout,
        "stderr": done.stderr,
        "exit": done.returncode,
        "ms": round((time.monotonic() - started) * 1000),
        "osrm": bool(use_osrm and SETTINGS["osrm"]),
        "revision": SETTINGS["revision"],
        "binary": SETTINGS["binary"],
    }


class Handler(BaseHTTPRequestHandler):
    def send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/sample": ("sample_in.json", "application/json; charset=utf-8"),
            "/vendor/leaflet.js": ("vendor/leaflet.js", "application/javascript"),
            "/vendor/leaflet.css": ("vendor/leaflet.css", "text/css"),
        }
        if path == "/health":
            return self.send(200, b'{"ok":true}', "application/json")
        if path == "/road":  # hình học đường bộ cho map: /road?from=lat,lng&to=lat,lng
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

            def point(name):
                parts = (query.get(name) or [""])[0].split(",")
                try:
                    return (float(parts[0]), float(parts[1])) if len(parts) == 2 else None
                except ValueError:
                    return None

            start, end = point("from"), point("to")
            if not start or not end:
                return self.send(400, b'{"error":"from/to can dang lat,lng"}', "application/json")
            points = road_points(start, end)
            return self.send(200, json.dumps({"points": points or [], "ok": points is not None}).encode(),
                             "application/json; charset=utf-8")
        if path not in files:
            return self.send(404, b"not found", "text/plain")
        name, content_type = files[path]
        file = ROOT / name
        if not file.exists():
            return self.send(404, f"thiếu {name}".encode(), "text/plain")
        self.send(200, file.read_bytes(), content_type)

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/plan":
            return self.send(404, b"not found", "text/plain")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", "replace")
        use_osrm = self.headers.get("X-Use-Osrm", "1") == "1"
        use_explain = self.headers.get("X-Explain", "1") == "1"
        try:
            json.loads(body)
        except json.JSONDecodeError as error:
            return self.send(400, json.dumps({"ok": False, "error": f"JSON hỏng: {error}"}).encode(),
                             "application/json; charset=utf-8")
        result = run_plan(body, use_osrm, use_explain)
        self.send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("review_map: " + (fmt % args) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--osrm", default="", help="URL OSRM, VD http://127.0.0.1:5000 (trống = chim bay)")
    parser.add_argument("--binary", default=SETTINGS["binary"])
    args = parser.parse_args()
    SETTINGS["binary"] = args.binary
    SETTINGS["osrm"] = args.osrm
    SETTINGS["revision"] = git_revision()
    if not Path(SETTINGS["binary"]).exists():
        sys.exit(f"không thấy binary: {SETTINGS['binary']} — build trước: cmake -S core -B core/build && cmake --build core/build -j")
    print(f"review_map: http://127.0.0.1:{args.port}  (binary {SETTINGS['binary']}, osrm {SETTINGS['osrm'] or 'không'})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
