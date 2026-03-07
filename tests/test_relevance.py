import unittest


from pipelines.relevance import compute_spirulina_relevance, is_spirulina_centric


class TestRelevance(unittest.TestCase):
    def test_spirulina_positive(self):
        rel = compute_spirulina_relevance(
            url="https://example.com/paper.pdf",
            title="Growth of Arthrospira platensis in Zarrouk medium",
            text="We cultivated Spirulina (Arthrospira) under alkaline conditions.",
        )
        self.assertGreater(rel.score, 0.5)
        self.assertTrue(is_spirulina_centric(rel.score, 0.30))

    def test_other_microalgae_negative_when_no_core(self):
        rel = compute_spirulina_relevance(
            url="https://example.com/chlorella",
            title="Chlorella vulgaris photobioreactor",
            text="Chlorella growth and lipid accumulation.",
        )
        self.assertLess(rel.score, 0.25)
        self.assertFalse(is_spirulina_centric(rel.score, 0.30))


if __name__ == "__main__":
    unittest.main()
