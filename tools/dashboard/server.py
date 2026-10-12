"""Dashboard theo dõi khi load test (Phase 8.3 phần 2): gom GET /metrics của các worker / gateway.

  python3 tools/dashboard/server.py --port 8095 \
      --targets worker1=http://127.0.0.1:18081,worker2=http://127.0.0.1:18082,gateway=http://127.0.0.1:8080
  rồi mở http://127.0.0.1:8095 (máy khác: ssh -N -L 8095:127.0.0.1:8095 user@server)

Chỉ dùng thư viện chuẩn. Server tự lấy /metrics (trình duyệt không phải gọi sang cổng khác → không vướng CORS,
một tunnel là đủ). Prod có Grafana thì không cần trang này.
"""
import argparse, json, os, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TARGETS = {}


def fetch(item):
    name, url = item
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/metrics", timeout=2) as response:
            return name, {"ok": True, "text": response.read().decode()}
    except Exception as error:  # tiến trình chết / chưa bật: dashboard vẫn chạy, báo DOWN
        return name, {"ok": False, "error": str(error)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/scrape":
            with ThreadPoolExecutor(len(TARGETS) or 1) as pool:
                body, kind = json.dumps(dict(pool.map(fetch, TARGETS.items()))).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            body, kind = open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # im lặng: trang gọi /scrape mỗi 2 giây
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument("--targets", required=True, help="tên=url,tên=url (url không kèm /metrics)")
    args = parser.parse_args()
    TARGETS.update(part.split("=", 1) for part in args.targets.split(","))
    print(f"dashboard: http://127.0.0.1:{args.port}  ·  nguồn: {TARGETS}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
