import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UiSecurityContractsTest(unittest.TestCase):
    def test_html_text_is_escaped_at_the_boundary(self):
        from nasdx.ui_security import escape_html

        hostile = '<img src=x onerror="alert(1)"><script>x</script>'
        escaped = escape_html(hostile)
        self.assertNotIn("<img", escaped)
        self.assertNotIn("<script", escaped)
        self.assertIn("&lt;img", escaped)
        self.assertIn("&quot;", escaped)

    def test_external_links_allow_only_absolute_http_urls(self):
        from nasdx.ui_security import safe_external_link

        self.assertIn('href="https://example.com/path"', safe_external_link("公告", "https://example.com/path"))
        for url in ["javascript:alert(1)", "data:text/html,x", "//example.com", "\nhttps://example.com"]:
            rendered = safe_external_link("<b>公告</b>", url)
            self.assertNotIn("href=", rendered)
            self.assertIn("&lt;b&gt;", rendered)

    def test_app_and_plan_tables_use_shared_html_and_url_safety_helpers(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        table_source = (ROOT / "nasdx" / "ui" / "plan_tables.py").read_text(encoding="utf-8")
        self.assertIn("from nasdx.ui_security import escape_html", source)
        self.assertIn("from nasdx.ui_security import escape_html, safe_external_link", table_source)
        self.assertIn("safe_external_link(", table_source)
        self.assertNotIn("f'<a href=", table_source)


if __name__ == "__main__":
    unittest.main()
