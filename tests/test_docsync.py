import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsync  # noqa: E402
from fake_confluence import FakeConfluence  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]


class TestMarkdownToStorage(unittest.TestCase):
    def test_code_block_becomes_code_macro_with_language(self):
        out = docsync.md_to_storage("```sql\nselect 1 -- a & b\n```\n")
        self.assertIn('<ac:structured-macro ac:name="code">', out)
        self.assertIn('<ac:parameter ac:name="language">sql</ac:parameter>', out)
        self.assertIn("<![CDATA[select 1 -- a & b]]>", out)

    def test_mermaid_block_has_no_language_but_a_title(self):
        out = docsync.md_to_storage("```mermaid\nflowchart LR\n A-->B\n```\n")
        self.assertNotIn('ac:name="language"', out)
        self.assertIn('<ac:parameter ac:name="title">mermaid</ac:parameter>', out)

    def test_note_blockquote_becomes_panel(self):
        out = docsync.md_to_storage("> **Warning:** late files are dropped.\n")
        self.assertIn('<ac:structured-macro ac:name="warning">', out)
        self.assertIn("late files are dropped", out)
        self.assertNotIn("<blockquote>", out)

    def test_md_link_becomes_page_link_with_prefixed_title(self):
        out = docsync.md_to_storage("see [lineage](20-data-lineage.md#hop) now", {"20-data-lineage.md": "[MMM] Data lineage"})
        self.assertIn('<ri:page ri:content-title="[MMM] Data lineage" />', out)
        self.assertIn("<ac:link-body>lineage</ac:link-body>", out)

    def test_unresolved_link_is_left_and_warned(self):
        w = []
        out = docsync.md_to_storage("[x](missing.md)", {}, w)
        self.assertIn('href="missing.md"', out)
        self.assertEqual(len(w), 1)

    def test_toc_marker_and_comments(self):
        out = docsync.md_to_storage("[TOC]\n\n<!-- guidance for authors -->\n\n## A\ntext\n")
        self.assertIn('<ac:structured-macro ac:name="toc">', out)
        self.assertNotIn("guidance for authors", out)

    def test_table_and_wellformed(self):
        out = docsync.md_to_storage("| a | b |\n|---|---|\n| 1 | x & y |\n")
        self.assertIn("<table>", out)
        self.assertIsNone(docsync.validate_storage(out))

    def test_cdata_terminator_is_split(self):
        self.assertEqual(docsync.cdata("a]]>b"), "<![CDATA[a]]]]><![CDATA[>b]]>")

    def test_all_templates_render_wellformed(self):
        for md in (PLUGIN / "templates" / "pages").glob("*.md"):
            _, body = docsync.parse_frontmatter(md.read_text())
            out = docsync.md_to_storage(body)
            self.assertIsNone(docsync.validate_storage(out), md.name)


class TestHelpers(unittest.TestCase):
    def test_frontmatter(self):
        meta, body = docsync.parse_frontmatter("---\ntitle: Pipelines\nparent: Start Here\norder: 3\n---\n# hi\n")
        self.assertEqual(meta, {"title": "Pipelines", "parent": "Start Here", "order": "3"})
        self.assertEqual(body, "# hi\n")

    def test_pr_from_subject(self):
        self.assertEqual(docsync.pr_from_subject("Merge pull request #42 from org/feature"), "42")
        self.assertEqual(docsync.pr_from_subject("Add dedup to fct_sales (#17)"), "17")
        self.assertIsNone(docsync.pr_from_subject("plain commit"))

    def test_parse_page_id(self):
        self.assertEqual(docsync.parse_page_id("https://x.atlassian.net/wiki/spaces/MMM/pages/123456/Title"), "123456")
        self.assertEqual(docsync.parse_page_id(" 77 "), "77")
        with self.assertRaises(ValueError):
            docsync.parse_page_id("nope")

    def test_bucket_for(self):
        cfg = dict(docsync.DEFAULT_CONFIG)
        self.assertEqual(docsync.bucket_for("docs/confluence/30-pipelines.md", cfg), "docs")
        self.assertEqual(docsync.bucket_for(".docsync/audit.jsonl", cfg), "docs")
        self.assertEqual(docsync.bucket_for("dbt/models/fct_sales.sql", cfg), "watch")
        self.assertEqual(docsync.bucket_for("tests/test_x.py", cfg), "ignore")
        self.assertEqual(docsync.bucket_for("README.md", cfg), "ignore")
        self.assertEqual(docsync.bucket_for("misc/diagram.png", cfg), "other")

    def test_audit_validation(self):
        good = {"summary": "s", "category": "tables", "verdict": "documented", "rationale": "r", "files": ["a.sql"], "pages": ["Tables"]}
        self.assertEqual(docsync.validate_audit_entry(good), [])
        bad = {"summary": "s", "category": "nope", "verdict": "documented", "rationale": "r", "files": ["a"]}
        probs = docsync.validate_audit_entry(bad)
        self.assertTrue(any("category" in p for p in probs))
        self.assertTrue(any("pages" in p for p in probs))

    def test_changelog_render_orders_newest_first_and_splits_excluded(self):
        entries = [
            {"ts": "2026-01-01T00:00:00Z", "pr": "1", "summary": "old", "category": "tables", "verdict": "documented", "rationale": "r", "pages": ["Tables"]},
            {"ts": "2026-02-01T00:00:00Z", "commit": "abc", "summary": "new|pipe", "category": "business-logic", "verdict": "documented", "rationale": "r", "pages": ["Pipelines"]},
            {"ts": "2026-03-01T00:00:00Z", "pr": "3", "summary": "rename var", "category": "other", "verdict": "excluded", "rationale": "refactor"},
        ]
        md = docsync.render_changelog(entries, "project")
        self.assertLess(md.index("new\\|pipe"), md.index("| old |"))
        self.assertIn("## Evaluated and excluded", md)
        self.assertIn("rename var", md.split("## Evaluated and excluded")[1])

    def test_topo_sort_parents_first(self):
        a = docsync.Page(Path("a.md"), "Child", "Parent", 0, "")
        b = docsync.Page(Path("b.md"), "Parent", None, 5, "")
        c = docsync.Page(Path("c.md"), "Aunt", None, 1, "")
        self.assertEqual([p.title for p in docsync.topo_sort([a, b, c])], ["Aunt", "Parent", "Child"])


class TestScopeGuard(unittest.TestCase):
    def test_update_outside_root_is_refused(self):
        c = FakeConfluence()
        with self.assertRaises(docsync.ScopeError):
            c.update_page("999", "Unrelated page", "<p>x</p>", "msg")
        self.assertFalse(any(m == "PUT" for m, _ in c.calls))

    def test_root_page_itself_is_never_overwritten(self):
        c = FakeConfluence()
        with self.assertRaises(docsync.ScopeError):
            c.update_page("100", "Root", "<p>x</p>", "msg")

    def test_create_under_foreign_parent_is_refused(self):
        c = FakeConfluence()
        with self.assertRaises(docsync.ScopeError):
            c.create_page("S1", "999", "New", "<p>x</p>")
        self.assertFalse(any(m == "POST" for m, _ in c.calls))

    def test_create_under_root_then_update_child_is_allowed(self):
        c = FakeConfluence()
        page = c.create_page("S1", "100", "Child", "<p>1</p>")
        c.update_page(page["id"], "Child", "<p>2</p>", "msg")
        self.assertEqual(c.pages[page["id"]]["version"]["number"], 2)


class TestEndToEnd(unittest.TestCase):
    """init a throwaway git repo from the templates, then sync twice against the fake server."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.name", "t"], check=True)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        docsync.main(["init", "--project-name", "TestProject", "--space", "MMM", "--root-page",
                      "https://x.atlassian.net/wiki/spaces/MMM/pages/100/Root", "--title-prefix", "[T] "])
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "init docsync (#7)"], check=True)
        self.cfg = docsync.load_config(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)

    def test_init_created_expected_files(self):
        for rel in (".docsync/config.yaml", ".docsync/project-context.md", ".docsync/audit.jsonl", ".docsync/bin/docsync.py",
                    "docs/confluence/00-start-here.md", ".github/workflows/docsync.yml"):
            self.assertTrue((self.tmp / rel).exists(), rel)
        self.assertEqual(self.cfg["confluence"]["root_page_id"], "100")
        self.assertEqual(self.cfg["confluence"]["title_prefix"], "[T] ")

    def test_sync_creates_then_is_idempotent_then_updates(self):
        c = FakeConfluence()
        res = docsync.sync(self.cfg, c, "abc123", "7", respect_manual=False, force=False, out_dir=None)
        self.assertEqual(len(res["created"]), 10)  # 9 templates + Change Log
        self.assertIn("[T] Change Log", res["created"])
        self.assertEqual(res["orphans"], [])
        self.assertTrue(all(pid != "999" for pid in c.props))  # never touched the unrelated page
        body = c.pages[c.by_title()["[T] Start Here"]["id"]]["body"]
        self.assertIn("Auto-maintained by docsync", body)
        self.assertIn("PR #7", body)
        self.assertIn('ri:content-title="[T] Data lineage"', body)

        res2 = docsync.sync(self.cfg, c, "abc123", "7", respect_manual=False, force=False, out_dir=None)
        self.assertEqual(res2["created"], [])
        self.assertEqual(res2["updated"], [])
        self.assertEqual(len(res2["unchanged"]), 10)

        page = self.tmp / "docs/confluence/80-glossary.md"
        page.write_text(page.read_text() + "| Uplift | Incremental sales attributable to marketing. |\n")
        res3 = docsync.sync(self.cfg, c, "def456", None, respect_manual=False, force=False, out_dir=None)
        self.assertEqual(res3["updated"], ["[T] Glossary"])
        gid = c.by_title()["[T] Glossary"]["id"]
        self.assertEqual(c.pages[gid]["version"]["number"], 2)
        self.assertIn("Uplift", c.pages[gid]["body"])
        self.assertEqual(c.props[gid]["value"]["version"], 2)

    def test_manual_edit_detected_and_respected_when_asked(self):
        c = FakeConfluence()
        docsync.sync(self.cfg, c, "a", None, respect_manual=False, force=False, out_dir=None)
        gid = c.by_title()["[T] Glossary"]["id"]
        c.pages[gid]["version"] = {"number": 5}  # someone edited in Confluence
        res = docsync.sync(self.cfg, c, "b", None, respect_manual=True, force=False, out_dir=None)
        self.assertEqual(res["manual_edits"], ["[T] Glossary"])
        self.assertEqual(res["updated"], [])
        res = docsync.sync(self.cfg, c, "c", None, respect_manual=False, force=False, out_dir=None)
        self.assertEqual(res["updated"], ["[T] Glossary"])

    def test_orphan_reported_not_deleted(self):
        c = FakeConfluence()
        c.pages["555"] = {"id": "555", "title": "[T] Old page", "parentId": "100", "spaceId": "S1", "version": {"number": 1}, "body": ""}
        res = docsync.sync(self.cfg, c, "a", None, respect_manual=False, force=False, out_dir=None)
        self.assertEqual(res["orphans"], ["[T] Old page"])
        self.assertIn("555", c.pages)

    def test_space_mismatch_refuses_to_write(self):
        c = FakeConfluence(space_key="OTHER")
        with self.assertRaises(SystemExit):
            docsync.sync(self.cfg, c, "a", None, respect_manual=False, force=False, out_dir=None)
        self.assertFalse(any(m in ("POST", "PUT") for m, _ in c.calls))

    def test_audit_add_and_changelog_page(self):
        entry = {"summary": "Dedup fct_sales by order line", "category": "business-logic", "verdict": "documented",
                 "rationale": "Changes reported sales totals", "files": ["dbt/models/fct_sales.sql"], "pages": ["Pipelines"]}
        saved = docsync.audit_add(self.cfg, entry)
        self.assertEqual(saved["branch"], "main")
        self.assertTrue(saved["ts"].endswith("Z"))
        c = FakeConfluence()
        docsync.sync(self.cfg, c, "a", "9", respect_manual=False, force=False, out_dir=None)
        body = c.pages[c.by_title()["[T] Change Log"]["id"]]["body"]
        self.assertIn("Dedup fct_sales by order line", body)

    def test_gate_fails_when_code_changes_without_docs(self):
        (self.tmp / "dbt").mkdir()
        (self.tmp / "dbt" / "fct.sql").write_text("select 1")
        subprocess.run(["git", "checkout", "-qb", "feature"], check=True)
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "logic change"], check=True)
        with self.assertRaises(SystemExit) as cm:
            docsync.main(["gate", "--base", "main"])
        self.assertEqual(cm.exception.code, 1)
        docsync.audit_add(self.cfg, {"summary": "x", "category": "other", "verdict": "excluded", "rationale": "refactor", "files": ["dbt/fct.sql"]})
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "audit"], check=True)
        docsync.main(["gate", "--base", "main"])  # passes: audit entry counts as evaluation

    def test_changed_buckets(self):
        ch = docsync.changed_files(self.cfg, "main")
        self.assertEqual(ch["branch"], "main")
        self.assertFalse(ch["watched_changed"])


if __name__ == "__main__":
    unittest.main()


class TestLocalPublishAndBackfill(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(docsync.DEFAULT_CONFIG)

    def test_pr_verdict_requires_docs_when_pipeline_code_changed(self):
        ok, why = docsync.pr_documentation_verdict(["dbt/models/fct.sql"], [], self.cfg)
        self.assertFalse(ok)
        ok, _ = docsync.pr_documentation_verdict(["dbt/models/fct.sql", ".docsync/audit.jsonl"], [], self.cfg)
        self.assertTrue(ok)
        ok, _ = docsync.pr_documentation_verdict(["dbt/models/fct.sql"], ["docs-not-needed"], self.cfg)
        self.assertTrue(ok)
        ok, why = docsync.pr_documentation_verdict(["README.md"], [], self.cfg)
        self.assertTrue(ok)
        self.assertIn("no pipeline code", why)

    def test_strip_publish_job_leaves_gate_only(self):
        import yaml
        t = (PLUGIN / "templates" / "github-workflow.yml").read_text()
        full = yaml.safe_load(t)
        local = yaml.safe_load(docsync.strip_publish_job(t))
        self.assertEqual(sorted(full["jobs"]), ["gate", "publish"])
        self.assertEqual(list(local["jobs"]), ["gate"])
        self.assertEqual(list(local[True] if True in local else local["on"]), ["pull_request"])
        self.assertNotIn("CONFLUENCE_API_TOKEN", docsync.strip_publish_job(t))

    def test_storage_to_text_flattens_tables_and_macros(self):
        xml = ('<h2>Owners</h2><table><tr><th>Who</th><th>What</th></tr><tr><td>Provider</td><td>lands files</td></tr></table>'
               '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[select 1]]></ac:plain-text-body></ac:structured-macro>')
        txt = docsync.storage_to_text(xml)
        self.assertIn("Owners", txt)
        self.assertIn("Provider | lands files", txt)
        self.assertIn("select 1", txt)
        self.assertNotIn("<", txt)

    def test_backfill_is_a_valid_audit_category(self):
        e = {"summary": "s", "category": "backfill", "verdict": "documented", "rationale": "r", "files": ["repo"], "pages": ["Start Here"]}
        self.assertEqual(docsync.validate_audit_entry(e), [])


class TestInitLocalMode(unittest.TestCase):
    def test_init_local_mode_writes_gate_only_workflow_and_config(self):
        import yaml
        tmp = Path(tempfile.mkdtemp()); cwd = os.getcwd(); os.chdir(tmp)
        try:
            subprocess.run(["git", "init", "-q", "-b", "main", "."], check=True)
            docsync.main(["init", "--project-name", "L", "--space", "X", "--root-page", "5", "--publish-mode", "local"])
            wf = yaml.safe_load((tmp / ".github/workflows/docsync.yml").read_text())
            self.assertEqual(list(wf["jobs"]), ["gate"])
            cfg = yaml.safe_load((tmp / ".docsync/config.yaml").read_text())
            self.assertEqual(cfg["publish_mode"], "local")
            self.assertIn(".docsync/imported/", (tmp / ".gitignore").read_text())
        finally:
            os.chdir(cwd); shutil.rmtree(tmp)


class TestPullWithFake(unittest.TestCase):
    def test_pull_saves_every_in_scope_page(self):
        tmp = Path(tempfile.mkdtemp()); cwd = os.getcwd(); os.chdir(tmp)
        try:
            subprocess.run(["git", "init", "-q", "-b", "main", "."], check=True)
            docsync.main(["init", "--project-name", "P", "--space", "MMM", "--root-page", "100"])
            fake = FakeConfluence()
            fake.pages["200"] = {"id": "200", "title": "Legacy owners", "parentId": "100", "spaceId": "S1",
                                 "version": {"number": 1}, "body": {"storage": {"value": "<p>Provider lands Mondays</p>"}}}
            fake.pages["100"]["body"] = {"storage": {"value": "<p>root</p>"}}
            fake.pages["999"]["body"] = {"storage": {"value": "<p>secret</p>"}}
            os.environ.update(CONFLUENCE_BASE_URL="https://x/wiki", CONFLUENCE_EMAIL="e", CONFLUENCE_API_TOKEN="t")
            orig = docsync.client_from_env
            docsync.client_from_env = lambda cfg, dry_run=False: fake
            try:
                docsync.main(["pull"])
            finally:
                docsync.client_from_env = orig
            idx = json.loads((tmp / ".docsync/imported/index.json").read_text())
            titles = {i["title"] for i in idx}
            self.assertEqual(titles, {"Root", "Legacy owners"})
            self.assertIn("Provider lands Mondays", (tmp / ".docsync/imported/Legacy_owners.txt").read_text())
        finally:
            os.chdir(cwd); shutil.rmtree(tmp)


class TestMultiProject(unittest.TestCase):
    """Two projects in one repo: a second `init --project-name` migrates the first project to .docsync/projects/<project>/,
    each project gates and syncs against its own docs dir, root page and audit log."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.tmp)], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(self.tmp), "config", "user.name", "t"], check=True)
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        docsync.main(["init", "--project-name", "alpha", "--space", "MMM", "--root-page", "100", "--title-prefix", "[A] "])
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "init alpha"], check=True)
        docsync.main(["init", "--project-name", "beta", "--space", "MMM", "--root-page", "200", "--title-prefix", "[B] "])
        subprocess.run(["git", "add", "-A"], check=True)
        subprocess.run(["git", "commit", "-qm", "init beta"], check=True)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)

    def test_second_init_migrates_first_project_and_creates_second(self):
        import yaml
        self.assertEqual(docsync.list_projects(self.tmp), ["alpha", "beta"])
        for rel in (".docsync/projects/alpha/config.yaml", ".docsync/projects/alpha/project-context.md", ".docsync/projects/alpha/audit.jsonl",
                    ".docsync/projects/beta/config.yaml", ".docsync/projects/beta/project-context.md", ".docsync/projects/beta/audit.jsonl",
                    "docs/confluence/00-start-here.md", "docs/beta/00-start-here.md"):
            self.assertTrue((self.tmp / rel).exists(), rel)
        shared = yaml.safe_load((self.tmp / ".docsync/config.yaml").read_text())
        self.assertEqual(shared["base_branch"], "main")
        self.assertNotIn("confluence", shared)
        beta_raw = yaml.safe_load((self.tmp / ".docsync/projects/beta/config.yaml").read_text())
        self.assertNotIn("publish_mode", beta_raw)          # inherited from the shared file
        a = docsync.load_config(self.tmp, "alpha"); b = docsync.load_config(self.tmp, "beta")
        self.assertEqual((a["docs_dir"], a["confluence"]["root_page_id"], a["_project_rel"]), ("docs/confluence", "100", ".docsync/projects/alpha/"))
        self.assertEqual((b["docs_dir"], b["confluence"]["root_page_id"], b["publish_mode"]), ("docs/beta", "200", "ci"))
        wf = (self.tmp / ".github/workflows/docsync.yml").read_text()
        self.assertIn('- "docs/confluence/**"', wf)
        self.assertIn('- "docs/beta/**"', wf)

    def test_ambiguous_project_dies_and_env_resolves_it(self):
        with self.assertRaises(SystemExit):
            docsync.load_config(self.tmp)
        os.environ["DOCSYNC_PROJECT"] = "beta"
        try:
            self.assertEqual(docsync.load_config(self.tmp)["project_name"], "beta")
        finally:
            del os.environ["DOCSYNC_PROJECT"]
        self.assertEqual([c["project_name"] for c in docsync.load_all_configs(self.tmp)], ["alpha", "beta"])

    def test_gate_is_per_project(self):
        a = docsync.load_config(self.tmp, "alpha"); b = docsync.load_config(self.tmp, "beta")
        (self.tmp / ".docsync/projects/alpha/config.yaml").write_text(
            (self.tmp / ".docsync/projects/alpha/config.yaml").read_text().replace('  - "**/*.sql"', '  - "alpha/**/*.sql"'))
        (self.tmp / ".docsync/projects/beta/config.yaml").write_text(
            (self.tmp / ".docsync/projects/beta/config.yaml").read_text().replace('  - "**/*.sql"', '  - "beta/**/*.sql"'))
        a = docsync.load_config(self.tmp, "alpha"); b = docsync.load_config(self.tmp, "beta")
        # beta's audit entry does not satisfy alpha's gate; alpha's docs do
        self.assertEqual(docsync.bucket_for(".docsync/projects/beta/audit.jsonl", a), "other")
        self.assertEqual(docsync.bucket_for(".docsync/projects/beta/audit.jsonl", b), "docs")
        self.assertEqual(docsync.bucket_for("docs/confluence/30-pipelines.md", a), "docs")
        self.assertEqual(docsync.bucket_for("docs/beta/30-pipelines.md", a), "ignore")   # another project's page is not alpha's docs
        self.assertEqual(docsync.bucket_for("alpha/models/x.sql", a), "watch")
        self.assertEqual(docsync.bucket_for("alpha/models/x.sql", b), "other")
        ok, _ = docsync.pr_documentation_verdict(["alpha/models/x.sql", ".docsync/projects/beta/audit.jsonl"], [], a)
        self.assertFalse(ok)
        ok, _ = docsync.pr_documentation_verdict(["alpha/models/x.sql", ".docsync/projects/alpha/audit.jsonl"], [], a)
        self.assertTrue(ok)

    def test_each_project_syncs_to_its_own_root(self):
        a = docsync.load_config(self.tmp, "alpha"); b = docsync.load_config(self.tmp, "beta")
        docsync.audit_add(b, {"summary": "s", "category": "tables", "verdict": "documented", "rationale": "r",
                              "files": ["beta/x.sql"], "pages": ["Tables and datasets"]})
        self.assertEqual(len(docsync.read_audit(a)), 0)
        self.assertEqual(len(docsync.read_audit(b)), 1)
        ca = FakeConfluence(); cb = FakeConfluence(root_page_id="200")
        ra = docsync.sync(a, ca, "abc", None, respect_manual=False, force=False, out_dir=None)
        rb = docsync.sync(b, cb, "abc", None, respect_manual=False, force=False, out_dir=None)
        self.assertIn("[A] Start Here", ra["created"]); self.assertIn("[B] Start Here", rb["created"])
        self.assertNotIn("[B] Start Here", ra["created"])
        self.assertEqual(docsync.scratch_dir(a, "out"), self.tmp / ".docsync/out/alpha")
        self.assertEqual(a["_project"], "alpha")

    def test_migrate_command_on_single_project_repo(self):
        tmp = Path(tempfile.mkdtemp()); os.chdir(tmp)
        try:
            subprocess.run(["git", "init", "-q", "-b", "main", "."], check=True)
            docsync.main(["init", "--project-name", "solo", "--space", "X", "--root-page", "5"])
            self.assertEqual(docsync.list_projects(tmp), [])
            docsync.main(["migrate"])
            self.assertEqual(docsync.list_projects(tmp), ["solo"])
            self.assertTrue((tmp / ".docsync/projects/solo/project-context.md").exists())
            self.assertFalse((tmp / ".docsync/project-context.md").exists())
            cfg = docsync.load_config(tmp)
            self.assertEqual((cfg["project_name"], cfg["docs_dir"], cfg["_project_rel"]), ("solo", "docs/confluence", ".docsync/projects/solo/"))
        finally:
            os.chdir(self.tmp); shutil.rmtree(tmp)


class TestLegacyCaseLayoutUpgrade(unittest.TestCase):
    """Repos set up before 0.3 used 'case' vocabulary: .docsync/cases/<case>/, case-context.md, case_name:.
    `upgrade` renames them; `load_config` reads case_name until then and refuses the old directory with a hint."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.cwd = os.getcwd(); os.chdir(self.tmp)
        subprocess.run(["git", "init", "-q", "-b", "main", "."], check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "config", "user.name", "t"], check=True)

    def tearDown(self):
        os.chdir(self.cwd); shutil.rmtree(self.tmp)

    def _rename_to_legacy(self, d: Path):
        (d / "case-context.md").write_text((d / "project-context.md").read_text()); (d / "project-context.md").unlink()
        cfg = d / "config.yaml"
        cfg.write_text(cfg.read_text().replace("project_name:", "case_name:"))

    def test_single_layout_reads_case_name_and_upgrade_renames(self):
        docsync.main(["init", "--project-name", "solo", "--space", "X", "--root-page", "5"])
        self._rename_to_legacy(self.tmp / ".docsync")
        subprocess.run(["git", "add", "-A"], check=True); subprocess.run(["git", "commit", "-qm", "legacy"], check=True)
        self.assertEqual(docsync.load_config(self.tmp)["project_name"], "solo")   # fallback while un-upgraded
        docsync.main(["upgrade"])
        self.assertTrue((self.tmp / ".docsync/project-context.md").exists())
        self.assertFalse((self.tmp / ".docsync/case-context.md").exists())
        self.assertIn("project_name:", (self.tmp / ".docsync/config.yaml").read_text())
        self.assertNotIn("case_name:", (self.tmp / ".docsync/config.yaml").read_text())
        self.assertEqual(docsync.upgrade_legacy_names(self.tmp), [])   # idempotent

    def test_multi_layout_dies_with_hint_then_upgrade_moves_cases_dir(self):
        docsync.main(["init", "--project-name", "alpha", "--space", "X", "--root-page", "5"])
        docsync.main(["init", "--project-name", "beta", "--space", "X", "--root-page", "6"])
        for d in (self.tmp / ".docsync/projects").iterdir():
            self._rename_to_legacy(d)
        shutil.move(str(self.tmp / ".docsync/projects"), str(self.tmp / ".docsync/cases"))
        subprocess.run(["git", "add", "-A"], check=True); subprocess.run(["git", "commit", "-qm", "legacy"], check=True)
        with self.assertRaises(SystemExit):
            docsync.load_config(self.tmp, "alpha")
        docsync.main(["upgrade"])
        self.assertEqual(docsync.list_projects(self.tmp), ["alpha", "beta"])
        self.assertFalse((self.tmp / ".docsync/cases").exists())
        self.assertTrue((self.tmp / ".docsync/projects/beta/project-context.md").exists())
        b = docsync.load_config(self.tmp, "beta")
        self.assertEqual((b["project_name"], b["_project_rel"]), ("beta", ".docsync/projects/beta/"))
