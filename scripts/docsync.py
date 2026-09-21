#!/usr/bin/env python3
"""
docsync — keep a scoped Confluence page tree in sync with docs-as-code markdown.

Source of truth: <repo>/<docs_dir>/*.md  (one file = one Confluence page; docs_dir is per case,
default docs/confluence for a single-case repo, docs/<case> when a repo holds several projects)
Config:          <repo>/.docsync/config.yaml
Audit log:       <repo>/.docsync/audit.jsonl   (one JSON object per line)

Subcommands
  init      bootstrap a case: config, brief, audit log, page templates, CI workflow (once per project)
  migrate   move a single-case repo to the multi-case layout .docsync/cases/<case>/
  doctor    verify credentials, root page, space and print the in-scope page tree
  changed   list files changed since a base ref, bucketed as watch/ignore/docs/other
  render    print the Confluence storage XML for one markdown page
  plan      dry run of push: create/update/unchanged/orphan per page, XML to .docsync/out/
  push      publish changed pages + regenerate the Change Log page from audit.jsonl
  audit     add / show / validate audit entries
  status    offline summary: pages, audit tail, docs diff vs base
  pull      download existing pages under the root to .docsync/imported/ (input for backfill)
  merge-publish  verify the PR is documented, merge it with gh, publish from trunk (no CI secrets needed)

Every write is checked against the configured root page: a page is only ever
created under, or updated within, the root page's subtree. Pages are never deleted.

Env: CONFLUENCE_BASE_URL (https://<site>.atlassian.net/wiki), CONFLUENCE_EMAIL,
     CONFLUENCE_API_TOKEN.  Optional: DOCSYNC_PR, DOCSYNC_COMMIT.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fnmatch
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None
try:
    import markdown as _markdown
except ImportError:  # pragma: no cover
    _markdown = None
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CONFIG_REL = Path(".docsync/config.yaml")
AUDIT_REL = Path(".docsync/audit.jsonl")
CONTEXT_REL = Path(".docsync/case-context.md")
CASES_REL = Path(".docsync/cases")           # multi-case layout: one sub-directory per project
SHARED_KEYS = ("base_branch", "publish_mode", "overwrite_manual_edits", "changelog_page")
PROP_KEY = "docsync"
CHANGELOG_TITLE = "Change Log"

AUDIT_CATEGORIES = {
    "business-logic",     # transformation / calculation / filter / join logic changed
    "storage-path",       # S3 / GCS / blob / landing path or bucket changed
    "tables",             # table, view, schema, grain or column set changed
    "new-feature",        # new dataset, pipeline, model or capability
    "ownership-process",  # who does what, cadence, SLA, hand-offs
    "data-quality",       # DQ rules, tests, thresholds, conventions
    "conventions",        # logging, naming, observability, design patterns
    "backfill",           # one-off reconciliation of docs against the whole codebase
    "other",
}
AUDIT_VERDICTS = {"documented", "excluded"}

DEFAULT_CONFIG: Dict = {
    "case_name": "",
    "confluence": {
        "base_url_env": "CONFLUENCE_BASE_URL",
        "space_key": "",
        "root_page_id": "",
        "title_prefix": "",
    },
    "docs_dir": "docs/confluence",
    "base_branch": "main",
    "watch_paths": ["**/*.sql", "**/*.py", "dbt/**", "airflow/**", "**/*.yml", "**/*.yaml", "**/*.tf"],
    "ignore_paths": ["tests/**", "**/*_test.py", "**/test_*.py", ".github/**", "**/*.md", "**/*.lock", "**/requirements*.txt"],
    "overwrite_manual_edits": True,
    "changelog_page": True,
}


# ----------------------------------------------------------------------------- utils
def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str, code: int = 2) -> None:
    log(f"error: {msg}")
    sys.exit(code)


def run_git(args: List[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    res = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def repo_root(start: Optional[Path] = None) -> Path:
    try:
        return Path(run_git(["rev-parse", "--show-toplevel"], cwd=start))
    except Exception:
        return (start or Path.cwd()).resolve()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deep_merge(base: Dict, override: Dict) -> Dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _read_yaml(path: Path) -> Dict:
    if yaml is None:
        die("pyyaml is required: pip install pyyaml")
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def list_cases(root: Path) -> List[str]:
    """Case names in the multi-case layout (.docsync/cases/<case>/config.yaml). Empty for a single-case repo."""
    d = root / CASES_REL
    return sorted(p.parent.name for p in d.glob("*/config.yaml")) if d.is_dir() else []


def load_config(root: Path, case: Optional[str] = None) -> Dict:
    """Effective config for one case.

    Layouts:
      single  .docsync/config.yaml + case-context.md + audit.jsonl          (what the first `init` creates)
      multi   .docsync/config.yaml  = shared settings (base_branch, publish_mode, ...)
              .docsync/cases/<case>/{config.yaml, case-context.md, audit.jsonl}   one per project / team
    `docsync migrate` (or a second `init --case-name`) moves a repo from single to multi.
    """
    cases = list_cases(root)
    shared_path = root / CONFIG_REL
    shared = _read_yaml(shared_path) if shared_path.exists() else None
    if not cases:
        if shared is None:
            die(f"{shared_path} not found. Run `docsync init` (or /docsync:init) in this repo first.")
        name = (shared.get("case_name") or "").strip()
        if case and case != name:
            die(f"no case '{case}' here; this repo has the single case '{name or '(unnamed)'}'")
        cfg = deep_merge(DEFAULT_CONFIG, shared)
        case_dir = root / ".docsync"
    else:
        case = case or os.environ.get("DOCSYNC_CASE") or (cases[0] if len(cases) == 1 else None)
        if not case:
            die(f"this repo has several docsync cases ({', '.join(cases)}). Pass --case <name> or set DOCSYNC_CASE.")
        if case not in cases:
            die(f"no case '{case}' under {CASES_REL}/ (have: {', '.join(cases)})")
        case_dir = root / CASES_REL / case
        cfg = deep_merge(deep_merge(DEFAULT_CONFIG, shared or {}), _read_yaml(case_dir / "config.yaml"))
        if not (cfg.get("case_name") or "").strip():
            cfg["case_name"] = case
    cfg["_root"] = str(root)
    cfg["_case_dir"] = str(case_dir)
    cfg["_case_rel"] = case_dir.relative_to(root).as_posix().rstrip("/") + "/"
    # the key used for --case, scratch dirs and per-case result maps: the directory name in the multi layout
    cfg["_case"] = case_dir.name if cases else (cfg.get("case_name") or "default")
    return cfg


def load_all_configs(root: Path, case: Optional[str] = None) -> List[Dict]:
    """Every case in the repo, or just `case`. A single-case repo yields one."""
    cases = list_cases(root)
    if case or not cases:
        return [load_config(root, case)]
    return [load_config(root, c) for c in cases]


def context_path(cfg: Dict) -> Path:
    return Path(cfg["_case_dir"]) / "case-context.md"


def scratch_dir(cfg: Dict, kind: str) -> Path:
    """Gitignored working dirs: .docsync/<kind>/ in a single-case repo, .docsync/<kind>/<case>/ with several."""
    base = Path(cfg["_root"]) / ".docsync" / kind
    return base / cfg["_case"] if list_cases(Path(cfg["_root"])) else base


def parse_page_id(value: str) -> str:
    """Accept a bare id or a Confluence URL like .../pages/123456/Title."""
    value = str(value).strip()
    m = re.search(r"/pages/(\d+)", value)
    if m:
        return m.group(1)
    if value.isdigit():
        return value
    raise ValueError(f"cannot parse a page id from {value!r}")


def detect_pr_number(explicit: Optional[str] = None, cwd: Optional[Path] = None) -> Optional[str]:
    if explicit:
        return str(explicit)
    env = os.environ.get("DOCSYNC_PR")
    if env:
        return env
    try:
        subject = run_git(["log", "-1", "--pretty=%s"], cwd=cwd)
    except Exception:
        return None
    return pr_from_subject(subject)


def pr_from_subject(subject: str) -> Optional[str]:
    m = re.search(r"Merge pull request #(\d+)", subject) or re.search(r"\(#(\d+)\)\s*$", subject)
    return m.group(1) if m else None


def detect_commit(explicit: Optional[str] = None, cwd: Optional[Path] = None) -> str:
    if explicit:
        return explicit[:12]
    env = os.environ.get("DOCSYNC_COMMIT")
    if env:
        return env[:12]
    try:
        return run_git(["rev-parse", "--short=12", "HEAD"], cwd=cwd)
    except Exception:
        return "unknown"


# ----------------------------------------------------------------------------- pages
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


class Page:
    def __init__(self, path: Path, title: str, parent: Optional[str], order: int, body: str):
        self.path = path
        self.title = title
        self.parent = parent
        self.order = order
        self.body = body
        self.storage: str = ""
        self.hash: str = ""

    def full_title(self, prefix: str) -> str:
        return f"{prefix}{self.title}"


def parse_frontmatter(text: str) -> Tuple[Dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: Dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, text[m.end():]


def load_pages(cfg: Dict) -> List[Page]:
    root = Path(cfg["_root"])
    docs_dir = root / cfg["docs_dir"]
    if not docs_dir.exists():
        die(f"docs dir {docs_dir} does not exist")
    pages: List[Page] = []
    for path in sorted(docs_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        title = meta.get("title") or re.sub(r"^\d+[-_]?", "", path.stem).replace("-", " ").strip().title()
        order = int(meta.get("order", "0") or 0)
        pages.append(Page(path, title, meta.get("parent") or None, order, body))
    titles = [p.title for p in pages]
    dupes = {t for t in titles if titles.count(t) > 1}
    if dupes:
        die(f"duplicate page titles in {docs_dir}: {sorted(dupes)}")
    by_title = {p.title: p for p in pages}
    for p in pages:
        if p.parent and p.parent not in by_title:
            die(f"{p.path.name}: parent '{p.parent}' is not a page in {docs_dir}")
    return topo_sort(pages)


def topo_sort(pages: List[Page]) -> List[Page]:
    """Parents before children, then by order/title."""
    by_title = {p.title: p for p in pages}
    depth: Dict[str, int] = {}

    def d(p: Page) -> int:
        if p.title in depth:
            return depth[p.title]
        depth[p.title] = 0 if not p.parent else 1 + d(by_title[p.parent])
        return depth[p.title]

    return sorted(pages, key=lambda p: (d(p), p.order, p.title.lower()))


# ----------------------------------------------------------------------------- markdown -> storage
LANG_MAP = {
    "py": "python", "python": "python", "sql": "sql", "yaml": "yaml", "yml": "yaml", "json": "json",
    "bash": "bash", "sh": "bash", "shell": "bash", "zsh": "bash", "js": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript", "java": "java", "scala": "scala", "xml": "xml", "html": "html",
    "text": "text", "txt": "text", "ini": "text", "toml": "text", "hcl": "text", "terraform": "text", "mermaid": None,
}
PANEL_MAP = {"note": "note", "info": "info", "tip": "tip", "warning": "warning", "warn": "warning"}


def cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _code_macro(lang: Optional[str], code: str, title: Optional[str] = None) -> str:
    params = ""
    if lang:
        params += f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
    if title:
        params += f'<ac:parameter ac:name="title">{html.escape(title)}</ac:parameter>'
    return f'<ac:structured-macro ac:name="code">{params}<ac:plain-text-body>{cdata(code)}</ac:plain-text-body></ac:structured-macro>'


def _replace_code_blocks(body: str) -> str:
    pat = re.compile(r'<pre><code(?: class="(?:language-)?([\w+-]+)")?>(.*?)</code></pre>', re.S)

    def repl(m: "re.Match") -> str:
        raw_lang = (m.group(1) or "").lower()
        code = html.unescape(m.group(2))
        if raw_lang in LANG_MAP:
            lang = LANG_MAP[raw_lang]
            title = "mermaid" if raw_lang == "mermaid" else None
        else:
            lang, title = None, (raw_lang or None)
        return _code_macro(lang, code.rstrip("\n"), title)

    return pat.sub(repl, body)


def _replace_panels(body: str) -> str:
    pat = re.compile(
        r"<blockquote>\s*<p><strong>(Note|Info|Tip|Warning|Warn)\s*:?\s*</strong>\s*:?\s*(.*?)</blockquote>", re.S | re.I)

    def repl(m: "re.Match") -> str:
        kind = PANEL_MAP[m.group(1).lower()]
        inner = "<p>" + m.group(2).strip()
        return f'<ac:structured-macro ac:name="{kind}"><ac:rich-text-body>{inner}</ac:rich-text-body></ac:structured-macro>'

    return pat.sub(repl, body)


def _replace_page_links(body: str, title_map: Dict[str, str], warnings: List[str]) -> str:
    """title_map: markdown filename (e.g. '10-lineage.md') -> full Confluence title."""
    pat = re.compile(r'<a href="(?:\./)?([\w./-]+\.md)(?:#[\w-]*)?">(.*?)</a>', re.S)

    def repl(m: "re.Match") -> str:
        fname = Path(m.group(1)).name
        target = title_map.get(fname)
        if not target:
            warnings.append(f"unresolved page link to {m.group(1)}")
            return m.group(0)
        return f'<ac:link><ri:page ri:content-title="{html.escape(target, quote=True)}" /><ac:link-body>{m.group(2)}</ac:link-body></ac:link>'

    return pat.sub(repl, body)


def md_to_storage(md: str, title_map: Optional[Dict[str, str]] = None, warnings: Optional[List[str]] = None) -> str:
    if _markdown is None:
        die("python-markdown is required: pip install markdown")
    warnings = warnings if warnings is not None else []
    title_map = title_map or {}
    md = re.sub(r"^\[TOC\]\s*$", "<p>DOCSYNC_TOC_MARKER</p>", md, flags=re.M)
    body = _markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists", "attr_list", "md_in_html"],
                              output_format="xhtml")
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = _replace_code_blocks(body)
    body = _replace_panels(body)
    body = _replace_page_links(body, title_map, warnings)
    body = body.replace("<p>DOCSYNC_TOC_MARKER</p>",
                        '<ac:structured-macro ac:name="toc"><ac:parameter ac:name="maxLevel">3</ac:parameter></ac:structured-macro>')
    # images are not uploaded as attachments; leave a visible pointer instead of a broken embed
    body = re.sub(r'<img [^>]*src="([^"]+)"[^>]*/?>',
                  lambda m: f'<p><em>[image: {html.escape(m.group(1))} — attach manually or link to the source]</em></p>', body)
    return body


def banner(commit: str, pr: Optional[str], source_rel: str, synced_at: str) -> str:
    pr_txt = f" (PR #{html.escape(str(pr))})" if pr else ""
    return (
        '<ac:structured-macro ac:name="info"><ac:rich-text-body>'
        f"<p><strong>Auto-maintained by docsync.</strong> Last synced {synced_at[:10]} from commit "
        f"<code>{html.escape(commit)}</code>{pr_txt}. Source: <code>{html.escape(source_rel)}</code>. "
        "Edit the source file in the repo; direct edits to this page are overwritten on the next merge."
        "</p></ac:rich-text-body></ac:structured-macro>"
    )


def validate_storage(storage: str) -> Optional[str]:
    """Return an error string if the storage XML is not well-formed, else None."""
    import xml.etree.ElementTree as ET
    probe = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)(\w+);", r"&amp;\1;", storage)
    wrapped = ('<root xmlns:ac="http://atlassian.com/content" xmlns:ri="http://atlassian.com/resource/identifier">'
               f"{probe}</root>")
    try:
        ET.fromstring(wrapped)
        return None
    except ET.ParseError as exc:
        return str(exc)


def content_hash(storage: str) -> str:
    return hashlib.sha256(storage.encode("utf-8")).hexdigest()[:16]


def render_pages(cfg: Dict, pages: List[Page]) -> List[str]:
    prefix = cfg["confluence"].get("title_prefix", "") or ""
    title_map = {p.path.name: p.full_title(prefix) for p in pages}
    warnings: List[str] = []
    for p in pages:
        w: List[str] = []
        p.storage = md_to_storage(p.body, title_map, w)
        p.hash = content_hash(p.storage)
        warnings.extend(f"{p.path.name}: {x}" for x in w)
        err = validate_storage(p.storage)
        if err:
            warnings.append(f"{p.path.name}: storage XML may be malformed: {err}")
    return warnings


# ----------------------------------------------------------------------------- audit log
def audit_path(cfg: Dict) -> Path:
    return Path(cfg.get("_case_dir") or (Path(cfg["_root"]) / ".docsync")) / "audit.jsonl"


def read_audit(cfg: Dict) -> List[Dict]:
    path = audit_path(cfg)
    if not path.exists():
        return []
    entries = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            die(f"{path}:{i}: invalid JSON ({exc})")
    return entries


def validate_audit_entry(e: Dict) -> List[str]:
    problems = []
    for k in ("summary", "category", "verdict", "rationale", "files"):
        if k not in e or e[k] in ("", None, []):
            problems.append(f"missing '{k}'")
    if e.get("category") not in AUDIT_CATEGORIES:
        problems.append(f"category must be one of {sorted(AUDIT_CATEGORIES)}")
    if e.get("verdict") not in AUDIT_VERDICTS:
        problems.append(f"verdict must be one of {sorted(AUDIT_VERDICTS)}")
    if e.get("verdict") == "documented" and not e.get("pages"):
        problems.append("a 'documented' entry must list the 'pages' it updated")
    if not isinstance(e.get("files", []), list):
        problems.append("'files' must be a list")
    return problems


def audit_add(cfg: Dict, entry: Dict) -> Dict:
    entry = dict(entry)
    entry.setdefault("ts", now_iso())
    root = Path(cfg["_root"])
    try:
        entry.setdefault("branch", run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root))
        entry.setdefault("commit", run_git(["rev-parse", "--short=12", "HEAD"], cwd=root))
        entry.setdefault("author", run_git(["config", "user.email"], cwd=root, check=False) or None)
    except Exception:
        pass
    entry.setdefault("pr", None)
    entry.setdefault("pages", [])
    problems = validate_audit_entry(entry)
    if problems:
        die("invalid audit entry: " + "; ".join(problems))
    path = audit_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def render_changelog(entries: List[Dict], case_name: str) -> str:
    """Markdown for the Change Log page, newest first."""
    documented = [e for e in entries if e.get("verdict") == "documented"]
    excluded = [e for e in entries if e.get("verdict") == "excluded"]
    key = lambda e: e.get("ts", "")  # noqa: E731
    lines = [
        "> **Info:** This page is generated from `.docsync/audit.jsonl`. Every merged change that alters what the "
        "data *means* or *where it lives* is recorded here. Refactors and immaterial infrastructure changes are "
        "listed separately at the bottom so you can see they were considered.",
        "",
        "## Documented changes",
        "",
        "| Date | PR / commit | What changed | Category | Pages updated | Why it matters |",
        "|---|---|---|---|---|---|",
    ]
    for e in sorted(documented, key=key, reverse=True):
        ref = f"PR #{e['pr']}" if e.get("pr") else f"`{e.get('commit', '')}`"
        pages = ", ".join(e.get("pages") or []) or "—"
        lines.append(f"| {e.get('ts', '')[:10]} | {ref} | {_cell(e.get('summary'))} | {e.get('category')} | "
                     f"{_cell(pages)} | {_cell(e.get('rationale'))} |")
    if not documented:
        lines.append("| — | — | No documented changes yet | — | — | — |")
    lines += ["", "## Evaluated and excluded", "",
              "Changes that were reviewed and judged not to affect how anyone should understand or use the data.", "",
              "| Date | PR / commit | Change | Reason excluded |", "|---|---|---|---|"]
    for e in sorted(excluded, key=key, reverse=True):
        ref = f"PR #{e['pr']}" if e.get("pr") else f"`{e.get('commit', '')}`"
        lines.append(f"| {e.get('ts', '')[:10]} | {ref} | {_cell(e.get('summary'))} | {_cell(e.get('rationale'))} |")
    if not excluded:
        lines.append("| — | — | — | — |")
    return "\n".join(lines) + "\n"


def _cell(text: Optional[str]) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


# ----------------------------------------------------------------------------- confluence client
class ScopeError(RuntimeError):
    pass


class Confluence:
    """Minimal Confluence Cloud REST v2 client. All writes are guarded by the root-page scope."""

    def __init__(self, base_url: str, email: str, token: str, root_page_id: str, dry_run: bool = False):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/wiki"):
            self.base_url += "/wiki"
        self.auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.root_page_id = str(root_page_id)
        self.dry_run = dry_run
        self._scope: Optional[Dict[str, Dict]] = None  # id -> {id,title,parentId}
        self.session = requests.Session() if requests else None

    # --- transport
    def _req(self, method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None) -> Dict:
        if requests is None:
            die("requests is required: pip install requests")
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {"Authorization": f"Basic {self.auth}", "Accept": "application/json", "Content-Type": "application/json"}
        res = self.session.request(method, url, params=params, headers=headers, data=json.dumps(body) if body is not None else None, timeout=60)
        if res.status_code >= 400:
            raise RuntimeError(f"Confluence {method} {path} -> {res.status_code}: {res.text[:500]}")
        return res.json() if res.text else {}

    def _paged(self, path: str, params: Optional[Dict] = None) -> List[Dict]:
        out: List[Dict] = []
        params = dict(params or {}, limit=250)
        nxt: Optional[str] = path
        while nxt:
            data = self._req("GET", nxt, params=params if nxt == path else None)
            out.extend(data.get("results", []))
            nxt = (data.get("_links") or {}).get("next")
            if nxt and not nxt.startswith("http"):
                # v2 returns '/wiki/api/v2/...'; base_url already ends in /wiki
                nxt = self.base_url[: -len("/wiki")] + nxt if nxt.startswith("/wiki") else self.base_url + nxt
        return out

    # --- reads
    def get_page(self, page_id: str, with_body: bool = False) -> Dict:
        params = {"body-format": "storage"} if with_body else None
        return self._req("GET", f"/api/v2/pages/{page_id}", params=params)

    def get_space(self, space_id: str) -> Dict:
        return self._req("GET", f"/api/v2/spaces/{space_id}")

    def children(self, page_id: str) -> List[Dict]:
        return self._paged(f"/api/v2/pages/{page_id}/children", {"type": "page"})

    def scope(self, refresh: bool = False) -> Dict[str, Dict]:
        """All pages under the root (inclusive), keyed by id."""
        if self._scope is not None and not refresh:
            return self._scope
        root = self.get_page(self.root_page_id)
        tree: Dict[str, Dict] = {self.root_page_id: {"id": self.root_page_id, "title": root["title"],
                                                     "parentId": None, "spaceId": root.get("spaceId"),
                                                     "version": (root.get("version") or {}).get("number")}}
        stack = [self.root_page_id]
        while stack:
            pid = stack.pop()
            for child in self.children(pid):
                cid = str(child["id"])
                tree[cid] = {"id": cid, "title": child["title"], "parentId": pid, "spaceId": child.get("spaceId"),
                             "version": None}
                stack.append(cid)
        self._scope = tree
        return tree

    def by_title(self) -> Dict[str, Dict]:
        return {v["title"]: v for v in self.scope().values()}

    def assert_in_scope(self, page_id: str, what: str) -> None:
        if str(page_id) not in self.scope():
            raise ScopeError(f"refusing to {what}: page {page_id} is not under root page {self.root_page_id}")

    def get_property(self, page_id: str) -> Optional[Dict]:
        data = self._req("GET", f"/api/v2/pages/{page_id}/properties", params={"key": PROP_KEY})
        results = data.get("results", [])
        return results[0] if results else None

    # --- writes (guarded)
    def create_page(self, space_id: str, parent_id: str, title: str, storage: str) -> Dict:
        self.assert_in_scope(parent_id, f"create '{title}'")
        body = {"spaceId": space_id, "status": "current", "title": title, "parentId": str(parent_id),
                "body": {"representation": "storage", "value": storage}}
        if self.dry_run:
            # Register the pretend page in scope so a later set_property / child create
            # under it passes assert_in_scope and by_title() during `plan`.
            fake_id = f"dry-{abs(hash(title)) % 10**6}"
            self._scope[fake_id] = {"id": fake_id, "title": title, "parentId": str(parent_id),
                                    "spaceId": space_id, "version": 1}
            return {"id": fake_id, "title": title, "version": {"number": 1}}
        page = self._req("POST", "/api/v2/pages", body=body)
        self._scope[str(page["id"])] = {"id": str(page["id"]), "title": title, "parentId": str(parent_id),
                                        "spaceId": space_id, "version": 1}
        return page

    def update_page(self, page_id: str, title: str, storage: str, message: str) -> Dict:
        self.assert_in_scope(page_id, f"update '{title}'")
        if str(page_id) == self.root_page_id:
            raise ScopeError("refusing to overwrite the root page itself; put content on child pages")
        current = self.get_page(page_id)
        next_version = (current.get("version") or {}).get("number", 0) + 1
        body = {"id": str(page_id), "status": "current", "title": title,
                "body": {"representation": "storage", "value": storage},
                "version": {"number": next_version, "message": message}}
        if self.dry_run:
            return {"id": str(page_id), "title": title, "version": {"number": next_version}}
        return self._req("PUT", f"/api/v2/pages/{page_id}", body=body)

    def set_property(self, page_id: str, value: Dict) -> None:
        self.assert_in_scope(page_id, "set property")
        if self.dry_run:
            return
        existing = self.get_property(page_id)
        if existing:
            ver = (existing.get("version") or {}).get("number", 1) + 1
            self._req("PUT", f"/api/v2/pages/{page_id}/properties/{existing['id']}",
                      body={"key": PROP_KEY, "value": value, "version": {"number": ver}})
        else:
            self._req("POST", f"/api/v2/pages/{page_id}/properties", body={"key": PROP_KEY, "value": value})


def client_from_env(cfg: Dict, dry_run: bool = False) -> Confluence:
    c = cfg["confluence"]
    base = os.environ.get(c.get("base_url_env", "CONFLUENCE_BASE_URL"), "")
    email = os.environ.get("CONFLUENCE_EMAIL", "")
    token = os.environ.get("CONFLUENCE_API_TOKEN", "")
    missing = [n for n, v in (("CONFLUENCE_BASE_URL", base), ("CONFLUENCE_EMAIL", email), ("CONFLUENCE_API_TOKEN", token)) if not v]
    if missing:
        die(f"missing environment variables: {', '.join(missing)}")
    if not c.get("root_page_id"):
        die(f"confluence.root_page_id is not set in {cfg.get('_case_rel', '.docsync/')}config.yaml")
    return Confluence(base, email, token, str(c["root_page_id"]), dry_run=dry_run)


# ----------------------------------------------------------------------------- sync engine
def sync(cfg: Dict, client: Confluence, commit: str, pr: Optional[str], respect_manual: bool,
         force: bool, out_dir: Optional[Path]) -> Dict:
    pages = load_pages(cfg)
    warnings = render_pages(cfg, pages)
    prefix = cfg["confluence"].get("title_prefix", "") or ""
    synced_at = now_iso()
    root = Path(cfg["_root"])

    if cfg.get("changelog_page", True):
        cl_md = render_changelog(read_audit(cfg), cfg.get("case_name", ""))
        cl = Page(audit_path(cfg), CHANGELOG_TITLE, None, 9999, cl_md)
        cl.storage = md_to_storage(cl_md)
        cl.hash = content_hash(cl.storage)
        pages.append(cl)

    scope = client.scope()
    root_info = scope[client.root_page_id]
    space_id = root_info.get("spaceId")
    expected_space = (cfg["confluence"].get("space_key") or "").strip()
    if expected_space and not client.dry_run:
        actual = client.get_space(space_id).get("key")
        if actual != expected_space:
            die(f"root page lives in space '{actual}' but config says '{expected_space}'. Refusing to write.")

    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    results = {"created": [], "updated": [], "unchanged": [], "manual_edits": [], "orphans": [], "warnings": warnings,
               "commit": commit, "pr": pr, "synced_at": synced_at, "dry_run": client.dry_run}
    local_titles = set()
    for p in pages:
        title = p.full_title(prefix)
        local_titles.add(title)
        parent_id = client.root_page_id if not p.parent else client.by_title()[f"{prefix}{p.parent}"]["id"]
        source_rel = str(p.path.relative_to(root)) if p.path.is_relative_to(root) else p.path.name
        full_storage = banner(commit, pr, source_rel, synced_at) + p.storage
        if out_dir:
            (out_dir / (re.sub(r"[^\w-]+", "_", title) + ".xml")).write_text(full_storage, encoding="utf-8")
        existing = client.by_title().get(title)
        prop_value = {"hash": p.hash, "commit": commit, "pr": pr, "synced_at": synced_at, "source": source_rel}
        if existing is None:
            page = client.create_page(space_id, parent_id, title, full_storage)
            prop_value["version"] = 1
            client.set_property(page["id"], prop_value)
            results["created"].append(title)
            continue
        pid = existing["id"]
        prop = None if client.dry_run and pid.startswith("dry-") else client.get_property(pid)
        stored = (prop or {}).get("value") or {}
        current = client.get_page(pid)
        cur_ver = (current.get("version") or {}).get("number")
        manually_edited = bool(stored) and stored.get("version") not in (None, cur_ver)
        if manually_edited:
            results["manual_edits"].append(title)
            if respect_manual:
                results["unchanged"].append(title)
                continue
        if stored.get("hash") == p.hash and not force and not manually_edited:
            results["unchanged"].append(title)
            continue
        msg = f"docsync: {commit}" + (f" (PR #{pr})" if pr else "")
        updated = client.update_page(pid, title, full_storage, msg)
        prop_value["version"] = (updated.get("version") or {}).get("number")
        client.set_property(pid, prop_value)
        results["updated"].append(title)

    for info in client.scope().values():
        if info["id"] == client.root_page_id:
            continue
        if info["title"] not in local_titles:
            results["orphans"].append(info["title"])
    return results


def print_sync_summary(res: Dict) -> None:
    tag = "DRY RUN — " if res["dry_run"] else ""
    log(f"\n{tag}docsync summary (commit {res['commit']}{', PR #' + str(res['pr']) if res['pr'] else ''})")
    for k in ("created", "updated", "unchanged"):
        for t in res[k]:
            log(f"  {k:<9} {t}")
    for t in res["manual_edits"]:
        log(f"  MANUAL EDIT DETECTED on Confluence page '{t}' — the repo version wins unless --respect-manual-edits")
    for t in res["orphans"]:
        log(f"  orphan    '{t}' exists in Confluence under the root but has no source file. Not deleted; archive it by hand or add a page.")
    for w in res["warnings"]:
        log(f"  warning   {w}")


# ----------------------------------------------------------------------------- changed files
def glob_match(path: str, pattern: str) -> bool:
    """fnmatch with '**/' allowed to match zero directories, so '**/*.md' matches 'README.md'."""
    if fnmatch.fnmatch(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:])


def bucket_for(path: str, cfg: Dict) -> str:
    docs_dir = cfg["docs_dir"].rstrip("/") + "/"
    case_rel = cfg.get("_case_rel", ".docsync/")   # ".docsync/" single-case, ".docsync/cases/<case>/" multi
    if path.startswith(docs_dir) or path.startswith(case_rel):
        return "docs"
    if any(glob_match(path, g) for g in cfg.get("ignore_paths", [])):
        return "ignore"
    if any(glob_match(path, g) for g in cfg.get("watch_paths", [])):
        return "watch"
    return "other"


def changed_files(cfg: Dict, base: Optional[str]) -> Dict:
    root = Path(cfg["_root"])
    base = base or cfg.get("base_branch", "main")
    # prefer origin/<base> when it exists so worktrees compare against the shared trunk
    candidates = [base] if "/" in base else [f"origin/{base}", base]
    merge_base = None
    for cand in candidates:
        try:
            merge_base = run_git(["merge-base", "HEAD", cand], cwd=root)
            base_used = cand
            break
        except Exception:
            continue
    if not merge_base:
        die(f"cannot find base ref (tried {candidates}). Pass --base <ref>.")
    raw = run_git(["diff", "--name-status", f"{merge_base}..HEAD"], cwd=root)
    files = []
    for line in raw.splitlines():
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        files.append({"path": path, "status": status, "bucket": bucket_for(path, cfg)})
    # include uncommitted work too — the developer may not have committed yet
    dirty = run_git(["status", "--porcelain"], cwd=root)
    for line in dirty.splitlines():
        path = line[3:].split(" -> ")[-1]
        if path and not any(f["path"] == path for f in files):
            files.append({"path": path, "status": "W", "bucket": bucket_for(path, cfg)})
    stat = run_git(["diff", "--stat", f"{merge_base}..HEAD"], cwd=root)
    return {
        "base": base_used, "merge_base": merge_base[:12], "head": detect_commit(cwd=root),
        "branch": run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root),
        "files": files,
        "watched_changed": any(f["bucket"] == "watch" for f in files),
        "docs_changed": any(f["bucket"] == "docs" for f in files),
        "stat": stat,
    }


# ----------------------------------------------------------------------------- init
def strip_publish_job(workflow: str) -> str:
    """Remove the CI publish job (for repos where secrets cannot be set; publish locally with merge-publish)."""
    workflow = re.sub(r"  # --- publish-job-start.*?  # --- publish-job-end\n", "", workflow, flags=re.S)
    workflow = re.sub(r"\n  push:\n    branches: \[main\]\n    paths:\n(?:      - .*\n)+", "\n", workflow)
    workflow = re.sub(r"  workflow_dispatch:\n(?:    .*\n)+", "", workflow)
    workflow = workflow.replace("# Job 2 (publish):    on merge to trunk, push changed pages + regenerate the Change Log page.",
                                "# Publishing:         local mode — run /docsync:merge (docsync.py merge-publish) after review.")
    return workflow.replace("# Required repo secrets: CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN\n",
                            "# No repo secrets required in local mode.\n")


SHARED_CONFIG_TEXT = """# docsync shared settings. Every case in .docsync/cases/<case>/config.yaml inherits these
# unless it sets its own. Secrets are NOT stored here: set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL,
# CONFLUENCE_API_TOKEN locally and as CI secrets.
#
# Add another project to this repo:
#   docsync init --case-name <name> --space <KEY> --root-page <page url> --title-prefix '[<name>] '
# It creates .docsync/cases/<name>/ and docs/<name>/ and adds the docs dir to the CI workflow.
base_branch: {base_branch}
publish_mode: {publish_mode}
overwrite_manual_edits: {overwrite_manual_edits}
changelog_page: {changelog_page}
"""


def _slug(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name.strip()).strip("-").lower() or "default"


def _move(root: Path, src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_git(["mv", str(src), str(dst)], cwd=root)
    except Exception:
        shutil.move(str(src), str(dst))


def _yaml_bool(v) -> str:
    return "true" if v in (True, None, "true", "True") else "false"


def migrate_to_cases(root: Path) -> Optional[str]:
    """Move a single-case repo to .docsync/cases/<case>/. Returns the case slug, or None if there is nothing to move."""
    if list_cases(root):
        return None
    legacy = root / CONFIG_REL
    if not legacy.exists():
        return None
    cfg = _read_yaml(legacy)
    if not (cfg.get("confluence") or {}).get("root_page_id") and not cfg.get("case_name"):
        return None   # already a shared-settings file, not a case
    slug = _slug(cfg.get("case_name") or root.name)
    case_dir = root / CASES_REL / slug
    case_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("config.yaml", "case-context.md", "audit.jsonl"):
        src = root / ".docsync" / fname
        if src.exists():
            _move(root, src, case_dir / fname)
    legacy.write_text(SHARED_CONFIG_TEXT.format(
        base_branch=cfg.get("base_branch", "main"), publish_mode=cfg.get("publish_mode", "ci"),
        overwrite_manual_edits=_yaml_bool(cfg.get("overwrite_manual_edits")),
        changelog_page=_yaml_bool(cfg.get("changelog_page"))), encoding="utf-8")
    return slug


def add_workflow_docs_path(wf_text: str, docs_dir_rel: str) -> str:
    """Add `<docs_dir>/**` to the publish job's `paths:` filter if it is not there yet."""
    line = f'      - "{docs_dir_rel.rstrip("/")}/**"'
    if line.strip() in wf_text:
        return wf_text
    m = re.search(r"\n\s+paths:\n", wf_text)
    if not m:
        return wf_text
    return wf_text[:m.end()] + line + "\n" + wf_text[m.end():]


def cmd_migrate(args: argparse.Namespace) -> None:
    root = repo_root()
    if list_cases(root):
        log("already in the multi-case layout: " + ", ".join(list_cases(root)))
        return
    slug = migrate_to_cases(root)
    if not slug:
        die(f"nothing to migrate: {CONFIG_REL} is missing or holds no case")
    print(json.dumps({"migrated_case": slug, "case_dir": str(CASES_REL / slug), "shared_config": str(CONFIG_REL)}, indent=2))
    log("Commit the moves. Commands find the case automatically while it is the only one; pass --case once there are several.")


def cmd_init(args: argparse.Namespace) -> None:
    root = repo_root()
    tpl = PLUGIN_ROOT / "templates"
    if not tpl.exists():
        die(f"templates not found at {tpl}; run init from the plugin checkout, not the vendored copy")
    case_name = (args.case_name or "").strip()
    cases = list_cases(root)
    single_cfg = root / CONFIG_REL

    # An existing single-case repo: same case => needs --force; different case => move it aside first.
    if single_cfg.exists() and not cases:
        existing = (_read_yaml(single_cfg).get("case_name") or "").strip()
        if not case_name or case_name == existing:
            if not args.force:
                die(f"{single_cfg} already exists for case '{existing}'. Use --force to overwrite its config, "
                    f"or --case-name <other project> to add a second case alongside it.")
        else:
            moved = migrate_to_cases(root)
            log(f"moved existing case '{moved}' to {CASES_REL / moved}/; shared settings now live in {CONFIG_REL}")
            cases = list_cases(root)

    if cases:
        if not case_name:
            die(f"this repo already has docsync cases ({', '.join(cases)}); pass --case-name <name> for the new project")
        slug = _slug(case_name)
        case_dir = root / CASES_REL / slug
        if (case_dir / "config.yaml").exists() and not args.force:
            die(f"case '{slug}' already exists at {case_dir}; use --force to overwrite its config only")
        docs_dir_rel = (args.docs_dir or f"docs/{slug}").rstrip("/")
    else:
        case_dir = root / ".docsync"
        docs_dir_rel = (args.docs_dir or "docs/confluence").rstrip("/")
    case_dir.mkdir(parents=True, exist_ok=True)

    root_page_id = parse_page_id(args.root_page) if args.root_page else ""
    config_text = (tpl / "config.yaml").read_text(encoding="utf-8")
    config_text = (config_text.replace("__PUBLISH_MODE__", args.publish_mode)
                   .replace("__CASE_NAME__", case_name or root.name)
                   .replace("__SPACE_KEY__", args.space or "")
                   .replace("__ROOT_PAGE_ID__", root_page_id)
                   .replace("__TITLE_PREFIX__", args.title_prefix or "")
                   .replace("__BASE_BRANCH__", args.base_branch or "main")
                   .replace("__DOCS_DIR__", docs_dir_rel))
    if cases:
        # shared keys live in .docsync/config.yaml; drop them from the case file so they are not duplicated
        config_text = re.sub(r"^(?:%s):.*\n" % "|".join(SHARED_KEYS), "", config_text, flags=re.M)
        config_text += ("\n# base_branch, publish_mode, overwrite_manual_edits and changelog_page are inherited from\n"
                        "# .docsync/config.yaml (shared by every case in this repo). Set one here only to override it.\n")
        if not single_cfg.exists():
            single_cfg.write_text(SHARED_CONFIG_TEXT.format(
                base_branch=args.base_branch or "main", publish_mode=args.publish_mode,
                overwrite_manual_edits="true", changelog_page="true"), encoding="utf-8")
    cfg_path = case_dir / "config.yaml"
    cfg_path.write_text(config_text, encoding="utf-8")
    created = [str(cfg_path.relative_to(root))]

    def copy_if_missing(src: Path, dst: Path) -> None:
        if dst.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        created.append(str(dst.relative_to(root)))

    copy_if_missing(tpl / "case-context.md", case_dir / "case-context.md")
    audit = case_dir / "audit.jsonl"
    if not audit.exists():
        audit.write_text("", encoding="utf-8")
        created.append(str(audit.relative_to(root)))
    docs_dir = root / docs_dir_rel
    for src in sorted((tpl / "pages").glob("*.md")):
        copy_if_missing(src, docs_dir / src.name)
    wf_dst = root / ".github" / "workflows" / "docsync.yml"
    if not wf_dst.exists():
        wf_text = (tpl / "github-workflow.yml").read_text(encoding="utf-8").replace("__DOCS_DIR__", docs_dir_rel)
        if args.publish_mode == "local":
            wf_text = strip_publish_job(wf_text)
        wf_dst.parent.mkdir(parents=True, exist_ok=True)
        wf_dst.write_text(wf_text, encoding="utf-8")
        created.append(str(wf_dst.relative_to(root)))
    else:
        wf_text = wf_dst.read_text(encoding="utf-8")
        updated = add_workflow_docs_path(wf_text, docs_dir_rel)
        if updated != wf_text:
            wf_dst.write_text(updated, encoding="utf-8")
            created.append(str(wf_dst.relative_to(root)) + " (added docs path)")
    # vendor the tool so CI and worktrees do not depend on the plugin being installed
    bin_dir = root / ".docsync" / "bin"
    bin_dir.mkdir(exist_ok=True)
    shutil.copyfile(SCRIPT_DIR / "docsync.py", bin_dir / "docsync.py")
    shutil.copyfile(SCRIPT_DIR / "requirements.txt", bin_dir / "requirements.txt")
    created += [".docsync/bin/docsync.py", ".docsync/bin/requirements.txt"]
    gitignore = root / ".gitignore"
    existing_ignore = gitignore.read_text() if gitignore.exists() else ""
    wanted = [l for l in (".docsync/out/", ".docsync/imported/") if l not in existing_ignore]
    if wanted:
        with open(gitignore, "a") as fh:
            fh.write("\n# docsync scratch (dry-run output, pulled Confluence pages)\n" + "\n".join(wanted) + "\n")
    print(json.dumps({"root": str(root), "case": case_name or root.name, "case_dir": str(case_dir.relative_to(root)),
                      "docs_dir": docs_dir_rel, "created": created}, indent=2))
    log(f"\nNext: fill in {case_dir.relative_to(root)}/case-context.md, set CONFLUENCE_* env vars, then run `docsync doctor`.")


def cmd_upgrade(args: argparse.Namespace) -> None:
    root = repo_root()
    bin_dir = root / ".docsync" / "bin"
    if not bin_dir.exists():
        die("no .docsync/bin in this repo; run init first")
    shutil.copyfile(SCRIPT_DIR / "docsync.py", bin_dir / "docsync.py")
    shutil.copyfile(SCRIPT_DIR / "requirements.txt", bin_dir / "requirements.txt")
    log(f"vendored copy updated in {bin_dir}")


# ----------------------------------------------------------------------------- commands
def cmd_doctor(args: argparse.Namespace) -> None:
    bad = False
    cfgs = load_all_configs(repo_root(), args.case)
    for cfg in cfgs:
        client = client_from_env(cfg)
        scope = client.scope()
        root = scope[client.root_page_id]
        space = client.get_space(root["spaceId"])
        ok_space = not cfg["confluence"].get("space_key") or space.get("key") == cfg["confluence"]["space_key"]
        print(f"case      : {cfg.get('case_name')}  ({cfg['_case_rel']}config.yaml)")
        print(f"root page : {root['title']} (id {client.root_page_id})")
        print(f"space     : {space.get('key')} — {space.get('name')}  {'OK' if ok_space else 'MISMATCH with config'}")
        print(f"in scope  : {len(scope) - 1} page(s) under root")
        for info in sorted(scope.values(), key=lambda x: x["title"]):
            if info["id"] != client.root_page_id:
                print(f"  - {info['title']} (id {info['id']})")
        pages = load_pages(cfg)
        warnings = render_pages(cfg, pages)
        print(f"local     : {len(pages)} page(s) in {cfg['docs_dir']}")
        for w in warnings:
            print(f"  warning: {w}")
        if len(cfgs) > 1:
            print()
        bad = bad or not ok_space
    if bad:
        sys.exit(1)


def storage_to_text(storage: str) -> str:
    """Crude readable text from storage XML so the agent can harvest existing content."""
    t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", storage, flags=re.S)
    t = re.sub(r"</(p|h[1-6]|li|tr|table|div)>", "\n", t)
    t = re.sub(r"</t[dh]>", " | ", t)
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"


def cmd_pull(args: argparse.Namespace) -> None:
    """Download every page under each case's root into .docsync/imported/[<case>/] (read-only; used by backfill)."""
    for cfg in load_all_configs(repo_root(), args.case):
        root = Path(cfg["_root"])
        client = client_from_env(cfg)
        out = scratch_dir(cfg, "imported")
        out.mkdir(parents=True, exist_ok=True)
        out_rel = out.relative_to(root).as_posix()
        index = []
        for info in client.scope().values():
            page = client.get_page(info["id"], with_body=True)
            storage = ((page.get("body") or {}).get("storage") or {}).get("value", "")
            slug = re.sub(r"[^\w-]+", "_", info["title"]).strip("_") or info["id"]
            (out / f"{slug}.xml").write_text(storage, encoding="utf-8")
            (out / f"{slug}.txt").write_text(storage_to_text(storage), encoding="utf-8")
            index.append({"id": info["id"], "title": info["title"], "parentId": info["parentId"],
                          "is_root": info["id"] == client.root_page_id, "words": len(storage_to_text(storage).split()),
                          "file": f"{out_rel}/{slug}.txt"})
        (out / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
        print(json.dumps(index, indent=2))
        log(f"[{cfg.get('case_name')}] {len(index)} page(s) saved under {out} (root included). These files are inputs, not published pages.")


def cmd_changed(args: argparse.Namespace) -> None:
    cfgs = load_all_configs(repo_root(), args.case)
    res = {cfg["_case"]: changed_files(cfg, args.base) for cfg in cfgs}
    print(json.dumps(res if len(cfgs) > 1 else next(iter(res.values())), indent=2))


def cmd_gate(args: argparse.Namespace) -> None:
    """CI gate, per case: watched code changed => that case's docs or audit log must have changed too."""
    cfgs = load_all_configs(repo_root(), args.case)
    failed = False
    for cfg in cfgs:
        ch = changed_files(cfg, args.base)
        watched = [f["path"] for f in ch["files"] if f["bucket"] == "watch"]
        docs = [f["path"] for f in ch["files"] if f["bucket"] == "docs"]
        if len(cfgs) > 1:
            print(f"case {cfg['case_name']}:")
        print(f"base {ch['base']} ({ch['merge_base']}) -> {ch['head']} on {ch['branch']}")
        print(f"watched files changed: {len(watched)}   docs/audit files changed: {len(docs)}")
        if watched and not docs:
            print(f"\nFAIL: pipeline code changed but neither {cfg['docs_dir']}/ nor {cfg['_case_rel']}audit.jsonl did.")
            print("Every change to watched code must be evaluated: run /docsync:update in the branch.")
            print("It will either update the relevant page(s) or record an 'excluded' audit entry explaining why not.")
            for f in watched[:50]:
                print(f"  - {f}")
            failed = True
        else:
            print("PASS")
        if len(cfgs) > 1:
            print()
    if failed:
        sys.exit(1)


def cmd_render(args: argparse.Namespace) -> None:
    target = Path(args.file).resolve()
    cfgs = load_all_configs(repo_root(), args.case)
    for cfg in cfgs:
        docs_dir = (Path(cfg["_root"]) / cfg["docs_dir"]).resolve()
        if docs_dir not in target.parents:
            continue
        pages = load_pages(cfg)
        render_pages(cfg, pages)
        for p in pages:
            if p.path.resolve() == target:
                print(p.storage)
                return
    die(f"{args.file} is not a page in {', '.join(c['docs_dir'] for c in cfgs)}")


def cmd_push(args: argparse.Namespace, dry_run: bool = False) -> None:
    root = repo_root()
    cfgs = load_all_configs(root, args.case)
    commit = detect_commit(args.commit, cwd=root)
    pr = detect_pr_number(args.pr, cwd=root)
    results = {}
    for cfg in cfgs:
        client = client_from_env(cfg, dry_run=dry_run)
        out_dir = scratch_dir(cfg, "out") if (dry_run or args.out) else None
        try:
            res = sync(cfg, client, commit, pr, respect_manual=args.respect_manual_edits or not cfg.get("overwrite_manual_edits", True),
                       force=args.force, out_dir=out_dir)
        except ScopeError as exc:
            die(str(exc), code=3)
        if len(cfgs) > 1:
            log(f"\n=== case {cfg['case_name']}")
        print_sync_summary(res)
        results[cfg["_case"]] = res
    if args.json:
        print(json.dumps(results if len(cfgs) > 1 else next(iter(results.values())), indent=2))


def gh_json(args: List[str], cwd: Path) -> Dict:
    res = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True, text=True)
    if res.returncode != 0:
        die(f"gh {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
    return json.loads(res.stdout) if res.stdout.strip() else {}


def pr_documentation_verdict(files: List[str], labels: List[str], cfg: Dict) -> Tuple[bool, str]:
    """Same rule as `gate`, applied to a PR's file list. Returns (ok, reason)."""
    if "docs-not-needed" in labels:
        return True, "label docs-not-needed present"
    watched = [f for f in files if bucket_for(f, cfg) == "watch"]
    docs = [f for f in files if bucket_for(f, cfg) == "docs"]
    if watched and not docs:
        return False, f"{len(watched)} pipeline file(s) changed but no {cfg['docs_dir']} or {cfg.get('_case_rel', '.docsync/')}audit.jsonl change"
    return True, "documented" if docs else "no pipeline code changed"


def trunk_worktree(root: Path, trunk: str) -> Tuple[Path, bool]:
    """Return a worktree checked out on the trunk branch (existing one, or a temporary detached one)."""
    porcelain = run_git(["worktree", "list", "--porcelain"], cwd=root)
    path, branch = None, None
    for line in porcelain.splitlines() + [""]:
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):])
        elif line.startswith("branch "):
            branch = line[len("branch "):]
        elif line == "" and path is not None:
            if branch == f"refs/heads/{trunk}":
                return path, False
            path, branch = None, None
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="docsync-trunk-"))
    run_git(["worktree", "add", "--detach", str(tmp), f"origin/{trunk}"], cwd=root)
    return tmp, True


def cmd_merge_publish(args: argparse.Namespace) -> None:
    """Local alternative to the CI publish job: verify, merge the PR with gh, publish from the merged trunk."""
    root = repo_root()
    cfgs = load_all_configs(root, args.case)
    trunk = cfgs[0].get("base_branch", "main")
    if shutil.which("gh") is None:
        die("GitHub CLI `gh` is required for merge-publish (brew install gh; gh auth login)")
    pr = gh_json(["pr", "view", str(args.pr), "--json",
                  "number,state,title,headRefName,baseRefName,mergeable,labels,files,reviewDecision,statusCheckRollup"], root)
    if pr.get("state") != "OPEN":
        die(f"PR #{args.pr} is {pr.get('state')}, not OPEN")
    if pr.get("baseRefName") != trunk:
        die(f"PR #{args.pr} targets '{pr.get('baseRefName')}' but docsync trunk is '{trunk}'")
    files = [f["path"] for f in pr.get("files", [])]
    labels = [l["name"] for l in pr.get("labels", [])]
    print(f"PR #{pr['number']}: {pr['title']}  ({pr['headRefName']} -> {trunk})")
    ok = True
    for cfg in cfgs:
        case_ok, reason = pr_documentation_verdict(files, labels, cfg)
        tag = f"[{cfg['case_name']}] " if len(cfgs) > 1 else ""
        print(f"{tag}documentation check: {'OK' if case_ok else 'FAIL'} — {reason}")
        ok = ok and case_ok
    if not ok and not args.force:
        die("refusing to merge an undocumented PR. Run /docsync:update on the branch, or pass --force.")
    failing = [c for c in pr.get("statusCheckRollup") or []
               if (c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT")]
    if failing and not args.force:
        die("PR has failing checks: " + ", ".join(c.get("name") or c.get("context", "?") for c in failing) + ". Pass --force to override.")
    if pr.get("mergeable") == "CONFLICTING":
        die("PR has merge conflicts; resolve them first")
    if args.dry_run:
        print("dry run: would merge and publish now")
        return

    merge_cmd = ["pr", "merge", str(args.pr), f"--{args.method}"]
    if args.delete_branch:
        merge_cmd.append("--delete-branch")
    res = subprocess.run(["gh", *merge_cmd], cwd=str(root), capture_output=True, text=True)
    if res.returncode != 0:
        die(f"gh {' '.join(merge_cmd)} failed: {res.stderr.strip() or res.stdout.strip()}")
    print(f"merged PR #{args.pr} ({args.method})")

    run_git(["fetch", "origin", trunk], cwd=root)
    merge_sha = run_git(["rev-parse", "--short=12", f"origin/{trunk}"], cwd=root)
    wt, temporary = trunk_worktree(root, trunk)
    try:
        if not temporary:
            run_git(["pull", "--ff-only", "origin", trunk], cwd=wt)
        results = {}
        for wt_cfg in load_all_configs(wt, args.case):
            client = client_from_env(wt_cfg, dry_run=False)
            result = sync(wt_cfg, client, merge_sha, str(args.pr), respect_manual=not wt_cfg.get("overwrite_manual_edits", True),
                          force=False, out_dir=None)
            if len(cfgs) > 1:
                log(f"\n=== case {wt_cfg['case_name']}")
            print_sync_summary(result)
            results[wt_cfg["_case"]] = result
        if args.json:
            print(json.dumps(results if len(results) > 1 else next(iter(results.values())), indent=2))
    except ScopeError as exc:
        die(str(exc), code=3)
    finally:
        if temporary:
            run_git(["worktree", "remove", "--force", str(wt)], cwd=root, check=False)


def cmd_audit(args: argparse.Namespace) -> None:
    cfg = load_config(repo_root(), args.case)   # one case at a time; dies with the case list when ambiguous
    if args.audit_cmd == "add":
        raw = args.json if args.json else sys.stdin.read()
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            die(f"--json is not valid JSON: {exc}")
        if args.pr:
            entry["pr"] = args.pr
        saved = audit_add(cfg, entry)
        print(json.dumps(saved, indent=2))
    elif args.audit_cmd == "show":
        entries = read_audit(cfg)
        for e in entries[-args.last:]:
            print(json.dumps(e))
        log(f"{len(entries)} entries total")
    elif args.audit_cmd == "validate":
        bad = 0
        for i, e in enumerate(read_audit(cfg), 1):
            problems = validate_audit_entry(e)
            if problems:
                bad += 1
                print(f"entry {i}: {'; '.join(problems)}")
        print("audit log OK" if not bad else f"{bad} invalid entries")
        sys.exit(1 if bad else 0)
    elif args.audit_cmd == "render":
        print(render_changelog(read_audit(cfg), cfg.get("case_name", "")))


def cmd_status(args: argparse.Namespace) -> None:
    cfgs = load_all_configs(repo_root(), args.case)
    if len(cfgs) > 1:
        print(f"cases     : {', '.join(c['_case'] for c in cfgs)}  (pass --case <name> to address one)\n")
    for cfg in cfgs:
        pages = load_pages(cfg)
        warnings = render_pages(cfg, pages)
        prefix = cfg["confluence"].get("title_prefix", "") or ""
        print(f"case      : {cfg.get('case_name')}  (config {cfg['_case_rel']}config.yaml, brief {cfg['_case_rel']}case-context.md)")
        print(f"root page : {cfg['confluence'].get('root_page_id')}  space: {cfg['confluence'].get('space_key')}  prefix: '{prefix}'")
        print(f"pages     : {len(pages)} in {cfg['docs_dir']}")
        for p in pages:
            indent = "    " if p.parent else "  "
            print(f"{indent}- {p.full_title(prefix)}  ({p.path.name}, {len(p.body.split())} words)")
        for w in warnings:
            print(f"  warning: {w}")
        entries = read_audit(cfg)
        print(f"audit     : {len(entries)} entries; last {min(5, len(entries))}:")
        for e in entries[-5:]:
            print(f"  {e.get('ts', '')[:10]} {e.get('verdict'):<10} {e.get('category'):<18} {e.get('summary')}")
        try:
            ch = changed_files(cfg, None)
            print(f"branch    : {ch['branch']} vs {ch['base']} — watched changed: {ch['watched_changed']}, docs changed: {ch['docs_changed']}")
            if ch["watched_changed"] and not ch["docs_changed"]:
                print("  -> watched code changed but no docs/audit changes yet. Run /docsync:update before opening the PR.")
        except SystemExit:
            pass
        if len(cfgs) > 1:
            print()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="docsync", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--case", help="which case (project) in a multi-case repo; default: all, or the only one")

    p = sub.add_parser("init", help="bootstrap a docsync case in the current repo (run once per project)")
    p.add_argument("--case-name", help="project name; required when the repo already has a case")
    p.add_argument("--docs-dir", help="where the pages live (default docs/confluence for the first case, docs/<case> after)")
    p.add_argument("--space", help="Confluence space key, e.g. MMM")
    p.add_argument("--root-page", help="root page id or URL; docsync only writes under this page")
    p.add_argument("--title-prefix", help="prefix for every page title so titles stay unique in the space, e.g. '[MMM] '")
    p.add_argument("--base-branch", default="main")
    p.add_argument("--publish-mode", choices=["ci", "local"], default="ci",
                   help="ci: GitHub Actions publishes on merge (needs repo secrets). local: you merge+publish with `merge-publish`")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("upgrade", help="refresh the vendored copy in .docsync/bin from the plugin")
    p.set_defaults(fn=cmd_upgrade)

    p = sub.add_parser("migrate", help="move a single-case repo to the multi-case layout .docsync/cases/<case>/")
    p.set_defaults(fn=cmd_migrate)

    p = sub.add_parser("doctor", parents=[common], help="check credentials, root page, space and list in-scope pages")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("pull", parents=[common], help="download existing pages under the root to .docsync/imported/ for backfill")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("changed", parents=[common], help="files changed vs base, bucketed watch/ignore/docs/other (JSON)")
    p.add_argument("--base")
    p.set_defaults(fn=cmd_changed)

    p = sub.add_parser("gate", parents=[common], help="CI gate: fail if watched code changed without docs/audit changes")
    p.add_argument("--base")
    p.set_defaults(fn=cmd_gate)

    p = sub.add_parser("render", parents=[common], help="print storage XML for one page")
    p.add_argument("file")
    p.set_defaults(fn=cmd_render)

    for name, dry in (("plan", True), ("push", False)):
        p = sub.add_parser(name, parents=[common], help="dry run of push" if dry else "publish changed pages to Confluence")
        p.add_argument("--commit")
        p.add_argument("--pr")
        p.add_argument("--force", action="store_true", help="republish even if content hash is unchanged")
        p.add_argument("--respect-manual-edits", action="store_true", help="skip pages edited directly in Confluence")
        p.add_argument("--out", action="store_true", help="also write rendered XML to .docsync/out/")
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=lambda a, d=dry: cmd_push(a, dry_run=d))

    p = sub.add_parser("merge-publish", parents=[common], help="verify docs, merge the PR with gh, publish from the merged trunk (no CI secrets needed)")
    p.add_argument("--pr", required=True)
    p.add_argument("--method", choices=["squash", "merge", "rebase"], default="squash")
    p.add_argument("--delete-branch", action="store_true")
    p.add_argument("--force", action="store_true", help="merge even if undocumented or checks failing")
    p.add_argument("--dry-run", action="store_true", help="verify only, do not merge or publish")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_merge_publish)

    p = sub.add_parser("audit", parents=[common], help="audit log operations")
    asub = p.add_subparsers(dest="audit_cmd", required=True)
    a = asub.add_parser("add"); a.add_argument("--json"); a.add_argument("--pr")
    a = asub.add_parser("show"); a.add_argument("--last", type=int, default=20)
    asub.add_parser("validate")
    asub.add_parser("render")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("status", parents=[common], help="offline summary of pages, audit and pending changes")
    p.set_defaults(fn=cmd_status)
    return ap


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
