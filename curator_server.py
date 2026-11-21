import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX_PATH = Path("amber_map_index.json")
HOST = "127.0.0.1"
PORT = 8080


class CuratorHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Prevent browser caching of JSON while curating
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/save":
            self.send_error(404, "Endpoint not found")
            return

        try:
            content_length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(content_length)
            updated_record = json.loads(raw_body.decode("utf-8"))

            filename = updated_record.get("filename")
            if not filename:
                self.send_error(400, "Missing filename")
                return

            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)

            found = False
            for i, rec in enumerate(records):
                if rec.get("filename") == filename:
                    records[i] = updated_record
                    found = True
                    break

            if not found:
                self.send_error(404, f"Record not found: {filename}")
                return

            # Write safely via temp file
            tmp_path = INDEX_PATH.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
            tmp_path.replace(INDEX_PATH)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        except Exception as e:
            self.send_error(500, str(e))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), CuratorHandler)
    print(f"Curator running at http://{HOST}:{PORT}/curator.html")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()