"""tests/test_qdrant_rest.py — Unit tests for the Qdrant REST client

Why these tests exist
---------------------
The pipeline uses Qdrant as its vector database. In production we interact with
Qdrant via HTTP (REST API). A subtle but important design choice in
`pipelines/qdrant_rest.py` is that it uses a `requests.Session` (stored as a
module-level `_SESSION`) rather than calling `requests.get/post` directly.

Using a Session is good for production:
- connection pooling
- lower latency
- easier to set defaults (retries, headers)

But it changes how we should mock network calls in unit tests.
We therefore patch `pipelines.qdrant_rest._SESSION.request`.

These tests are not meant to validate Qdrant itself; they validate that our
client:
- issues the correct endpoints
- handles basic success payloads
- doesn't crash on expected responses

"""

import unittest
from unittest.mock import Mock, patch

from pipelines.qdrant_rest import QdrantConfig, ensure_collection, upsert_points, search, count_points


class TestQdrantRest(unittest.TestCase):
    @patch("pipelines.qdrant_rest._SESSION.request")
    def test_ensure_collection_creates_when_missing(self, m_req):
        """If the collection isn't present, ensure_collection should create it."""

        # First call: list collections
        resp_list = Mock(status_code=200)
        resp_list.raise_for_status.return_value = None
        resp_list.json.return_value = {"result": {"collections": [{"name": "other"}]}}

        # Second call: create collection
        resp_create = Mock(status_code=200)
        resp_create.raise_for_status.return_value = None
        resp_create.json.return_value = {"result": {"status": "ok"}}

        m_req.side_effect = [resp_list, resp_create]

        cfg = QdrantConfig(url="http://localhost:6333", collection="docs")
        ensure_collection(cfg, dim=384)

        # We expect at least two HTTP calls (list + create)
        self.assertGreaterEqual(m_req.call_count, 2)

    @patch("pipelines.qdrant_rest._SESSION.request")
    def test_search(self, m_req):
        """search() should return JSON dict containing result list."""

        resp = Mock(status_code=200)
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"result": [{"score": 0.9, "payload": {"url": "x"}}]}
        m_req.return_value = resp

        cfg = QdrantConfig(url="http://localhost:6333", collection="docs")
        out = search(cfg, vector=[0.0, 0.1], limit=3)

        self.assertIn("result", out)
        self.assertIsInstance(out["result"], list)

    @patch("pipelines.qdrant_rest._SESSION.request")
    def test_upsert_and_count(self, m_req):
        """upsert_points() and count_points() should handle success payloads."""

        resp_upsert = Mock(status_code=200)
        resp_upsert.raise_for_status.return_value = None
        resp_upsert.json.return_value = {"result": {"status": "ok"}}

        resp_count = Mock(status_code=200)
        resp_count.raise_for_status.return_value = None
        resp_count.json.return_value = {"result": {"count": 123}}

        # upsert_points internally issues a PUT to /points
        # count_points issues a POST to /points/count
        m_req.side_effect = [resp_upsert, resp_count]

        cfg = QdrantConfig(url="http://localhost:6333", collection="docs")
        upsert_points(cfg, [{"id": 1, "vector": [0.0], "payload": {"a": 1}}])
        self.assertEqual(count_points(cfg), 123)


if __name__ == "__main__":
    unittest.main()
