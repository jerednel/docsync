"""In-memory stand-in for the Confluence Cloud v2 endpoints docsync uses."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import docsync  # noqa: E402


class FakeConfluence(docsync.Confluence):
    def __init__(self, root_page_id="100", space_id="S1", space_key="MMM"):
        super().__init__("https://x.atlassian.net/wiki", "e@x", "tok", root_page_id)
        self.space = {"id": space_id, "key": space_key, "name": "MMM space"}
        self.pages = {
            root_page_id: {"id": root_page_id, "title": "Root", "parentId": None, "spaceId": space_id,
                           "version": {"number": 3}, "body": ""},
            "999": {"id": "999", "title": "Unrelated page", "parentId": None, "spaceId": space_id,
                    "version": {"number": 1}, "body": ""},
        }
        self.props = {}   # page_id -> {"id":..., "key":..., "value":..., "version":{"number":n}}
        self.calls = []
        self._next = 1000

    def _req(self, method, path, params=None, body=None):
        self.calls.append((method, path))
        m = re.match(r"^/api/v2/pages/(\w+)/children$", path)
        if m and method == "GET":
            kids = [p for p in self.pages.values() if p["parentId"] == m.group(1)]
            return {"results": [{"id": k["id"], "title": k["title"], "spaceId": k["spaceId"]} for k in kids], "_links": {}}
        m = re.match(r"^/api/v2/pages/(\w+)/properties$", path)
        if m:
            pid = m.group(1)
            if method == "GET":
                p = self.props.get(pid)
                return {"results": [p] if p else []}
            if method == "POST":
                self.props[pid] = {"id": f"prop-{pid}", "key": body["key"], "value": body["value"], "version": {"number": 1}}
                return self.props[pid]
        m = re.match(r"^/api/v2/pages/(\w+)/properties/([\w-]+)$", path)
        if m and method == "PUT":
            self.props[m.group(1)] = {"id": m.group(2), "key": body["key"], "value": body["value"], "version": body["version"]}
            return self.props[m.group(1)]
        m = re.match(r"^/api/v2/pages/(\w+)$", path)
        if m:
            pid = m.group(1)
            if pid not in self.pages:
                raise RuntimeError(f"Confluence GET {path} -> 404")
            if method == "GET":
                return dict(self.pages[pid])
            if method == "PUT":
                page = self.pages[pid]
                page["title"] = body["title"]
                page["body"] = body["body"]["value"]
                page["version"] = {"number": body["version"]["number"]}
                return dict(page)
        if path == "/api/v2/pages" and method == "POST":
            pid = str(self._next); self._next += 1
            self.pages[pid] = {"id": pid, "title": body["title"], "parentId": body["parentId"], "spaceId": body["spaceId"],
                               "version": {"number": 1}, "body": body["body"]["value"]}
            return dict(self.pages[pid])
        m = re.match(r"^/api/v2/spaces/(\w+)$", path)
        if m and method == "GET":
            return dict(self.space)
        raise AssertionError(f"unhandled fake request {method} {path}")
