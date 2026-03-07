import unittest


from pipelines.common import normalize_url, clean_text_with_stats, chunk_text


class TestCommon(unittest.TestCase):
    def test_normalize_url_drops_fragment_and_tracking(self):
        u = "HTTPS://Example.com/a/b/?utm_source=x&x=1#section"
        self.assertEqual(normalize_url(u), "https://example.com/a/b?x=1")

    def test_normalize_url_trailing_slash(self):
        self.assertEqual(normalize_url("https://example.com/a/"), "https://example.com/a")

    def test_clean_text_removes_boilerplate(self):
        raw = """
        Skip to main content
        Advertisement
        Spirulina (Arthrospira) grows well at alkaline pH.
        Privacy Policy
        """.strip()
        cleaned, stats = clean_text_with_stats(raw)
        self.assertIn("Spirulina", cleaned)
        self.assertNotIn("Advertisement", cleaned)
        self.assertGreater(stats.boilerplate_share, 0.3)

    def test_chunk_text_overlap(self):
        text = "x" * 1000
        chunks = chunk_text(text, max_chars=400, overlap=50)
        self.assertGreaterEqual(len(chunks), 3)
        # overlap check (approx)
        self.assertEqual(chunks[0][-50:], chunks[1][:50])


if __name__ == "__main__":
    unittest.main()
