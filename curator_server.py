import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

INDEX_PATH = Path("amber_map_index.json")
HOST = "127.0.0.1"
PORT = 8080


def find_record_index(records: list, updated_record: dict) -> int:
    """Locate a record uniquely by relative_path first, falling back to (compendium, filename) then filename."""
    rel_path = updated_record.get("relative_path")
    if rel_path:
        for i, rec in enumerate(records):
            if rec.get("relative_path") == rel_path:
                return i

    filename = updated_record.get("filename")
    compendium = updated_record.get("compendium")
    if filename and compendium:
        for i, rec in enumerate(records):
            if rec.get("filename") == filename and rec.get("compendium") == compendium:
                return i

    if filename:
        for i, rec in enumerate(records):
            if rec.get("filename") == filename:
                return i

    return -1


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

            if not updated_record.get("relative_path") and not updated_record.get("filename"):
                self.send_error(400, "Missing relative_path or filename")
                return

            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                records = json.load(f)

            idx = find_record_index(records, updated_record)
            if idx == -1:
                ident = updated_record.get("relative_path") or updated_record.get("filename")
                self.send_error(404, f"Record not found: {ident}")
                return

            existing = records[idx]
            # Enforce immutability of third-party module attribution fields
            if existing.get("managed_by_vault") is False:
                updated_record["managed_by_vault"] = False
                updated_record["compendium"] = existing.get("compendium")
                updated_record["relative_path"] = existing.get("relative_path")
                updated_record["filename"] = existing.get("filename")
            else:
                updated_record.setdefault("managed_by_vault", True)
                if not (updated_record.get("compendium") or "").strip():
                    updated_record["compendium"] = existing.get("compendium", "Shadow Vault")

            records[idx] = updated_record

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
