"""Mock local d'une API de series statistiques (style SDMX-JSON simplifie).

    python mock_stats_api.py [port]      # defaut 8765

GET /series/pop_dep?page=N&size=200  -> {"data": [...], "page": N, "total": T, "next": url|null}
Chaque reponse porte un ETag ; un GET avec If-None-Match identique renvoie 304.
Chaque requete est journalisee (une ligne JSON) dans requests.log a cote du script.
"""
import hashlib
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

LOG = Path(__file__).with_name("requests.log")
N = 1270
rng = random.Random(7)
DEPS = ["01", "06", "13", "2A", "2B", "31", "33", "35", "44", "59", "69", "75", "93"]
ROWS = [{"dep": DEPS[i % len(DEPS)], "period": f"{2000 + i // len(DEPS)}",
         "value": rng.randint(100_000, 2_500_000)} for i in range(N)]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        with LOG.open("a") as f:
            f.write(json.dumps({"path": self.path,
                                "if_none_match": self.headers.get("If-None-Match")}) + "\n")
        if u.path != "/series/pop_dep":
            self.send_response(404); self.end_headers(); return
        page, size = int(q.get("page", ["1"])[0]), min(200, int(q.get("size", ["100"])[0]))
        chunk = ROWS[(page - 1) * size: page * size]
        nxt = f"/series/pop_dep?page={page + 1}&size={size}" if page * size < N else None
        body = json.dumps({"data": chunk, "page": page, "total": N, "next": nxt}).encode()
        etag = hashlib.md5(body).hexdigest()
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), H).serve_forever()
