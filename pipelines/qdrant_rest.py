"""
Tiny Qdrant REST client (dependency-free).

Why:
- Avoid qdrant-client dependency issues.
- Keep behavior consistent across scripts (index/query/rag).
- Provide clearer errors + retries/backoff.

API references:
- Upsert points uses PUT /collections/{collection}/points?wait=true  (wait is a query param)  # v1.17.x
- Search points uses POST /collections/{collection}/points/search with with_payload / with_vector.
"""

from __future__ import annotations

import json as _json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests


# Reuse connections (important for indexing batches)
_SESSION = requests.Session()


class QdrantHTTPError(RuntimeError):
    pass


@dataclass(frozen=True)
class QdrantConfig:
    url: str
    collection: str
    api_key: Optional[str] = None

    @property
    def base_url(self) -> str:
        return (self.url or "").rstrip("/")


def _headers(cfg: QdrantConfig) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if cfg.api_key:
        # Qdrant Cloud uses header: api-key
        h["api-key"] = cfg.api_key
    return h


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {}


def _request(
    cfg: QdrantConfig,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[dict] = None,
    timeout_s: int = 30,
    retries: int = 2,
    retry_backoff_base_s: float = 0.6,
    ok_status: Tuple[int, ...] = (200, 201, 202),
    allow_404: bool = False,
    allow_409: bool = False,
) -> requests.Response:
    """
    Low-level HTTP wrapper with retries.
    Retries on: network errors, 429, 5xx.
    """
    url = f"{cfg.base_url}{path}"
    last_err: Optional[Exception] = None

    # Qdrant also supports server-side 'timeout' query param for some endpoints.
    # Here timeout_s is the HTTP timeout. (Callers may also pass Qdrant timeout in params.)
    http_timeout = max(5, int(timeout_s))

    for i in range(retries + 1):
        try:
            resp = _SESSION.request(
                method=method.upper(),
                url=url,
                params=params,
                json=payload,
                headers=_headers(cfg),
                timeout=http_timeout,
            )

            if allow_404 and resp.status_code == 404:
                return resp
            if allow_409 and resp.status_code == 409:
                return resp

            # Retry on rate limits / transient server errors
            if resp.status_code in (429, 500, 502, 503, 504):
                if i >= retries:
                    raise QdrantHTTPError(
                        f"Qdrant {method} {path} failed {resp.status_code}: {resp.text[:1200]}"
                    )
                time.sleep(retry_backoff_base_s * (2**i))
                continue

            if resp.status_code not in ok_status:
                raise QdrantHTTPError(
                    f"Qdrant {method} {path} failed {resp.status_code}: {resp.text[:2000]}"
                )

            return resp

        except Exception as e:
            last_err = e
            if i >= retries:
                raise
            time.sleep(retry_backoff_base_s * (2**i))

    raise QdrantHTTPError(str(last_err))


def collection_exists(cfg: QdrantConfig) -> bool:
    # Prefer direct get (cheaper + precise)
    r = _request(cfg, "GET", f"/collections/{cfg.collection}", timeout_s=15, retries=1, allow_404=True)
    return r.status_code == 200


def get_collection_info(cfg: QdrantConfig) -> dict:
    r = _request(cfg, "GET", f"/collections/{cfg.collection}", timeout_s=15, retries=1)
    return _safe_json(r)


def _extract_vector_size_distance(collection_info: dict) -> Tuple[Optional[int], Optional[str]]:
    """
    Return (size, distance) if we can infer it for the "single unnamed vector" mode.
    If collection is named-vectors, returns (None, None).
    """
    try:
        vectors = (
            (collection_info.get("result") or {})
            .get("config", {})
            .get("params", {})
            .get("vectors")
        )
        if isinstance(vectors, dict):
            # Single unnamed mode: {"size": 384, "distance": "Cosine"}
            if "size" in vectors and "distance" in vectors:
                return int(vectors["size"]), str(vectors["distance"])
            # Named mode: {"default": {"size":..., "distance":...}, ...}
            # We intentionally do not assume a name here.
            return None, None
    except Exception:
        pass
    return None, None


def create_collection(cfg: QdrantConfig, *, dim: int, distance: str = "Cosine") -> None:
    payload = {"vectors": {"size": int(dim), "distance": str(distance)}}
    # PUT create collection; 409 means it already exists (safe in concurrent runs)
    _request(
        cfg,
        "PUT",
        f"/collections/{cfg.collection}",
        payload=payload,
        timeout_s=30,
        retries=1,
        ok_status=(200, 201, 202),
        allow_409=True,
    )


def ensure_collection(cfg: QdrantConfig, *, dim: int, distance: str = "Cosine") -> None:
    if not collection_exists(cfg):
        create_collection(cfg, dim=dim, distance=distance)
        return

    # Optional sanity check: if existing collection has a different dim, fail early with a clear error.
    info = get_collection_info(cfg)
    existing_dim, existing_dist = _extract_vector_size_distance(info)
    if existing_dim is not None and existing_dim != int(dim):
        raise QdrantHTTPError(
            f"Collection '{cfg.collection}' exists but vector dim mismatch: existing={existing_dim}, expected={dim}. "
            f"Drop/recreate the collection or use a different name."
        )
    if existing_dist is not None and str(existing_dist).lower() != str(distance).lower():
        # Not fatal for cosine vs dot in some setups, but better to be explicit.
        raise QdrantHTTPError(
            f"Collection '{cfg.collection}' exists but distance mismatch: existing={existing_dist}, expected={distance}."
        )


def upsert_points(cfg: QdrantConfig, points: List[dict], *, wait: bool = True, timeout: int = 60) -> None:
    """
    Upsert points into Qdrant.

    Qdrant API: PUT /collections/{collection}/points?wait=true&timeout=60
    Body: {"points": [...]}
    """
    if not points:
        return

    # server-side timeout is in seconds; keep it bounded
    q_timeout = max(1, int(timeout))
    params = {
        "wait": "true" if wait else "false",
        "timeout": str(q_timeout),
    }

    payload = {"points": points}

    # HTTP timeout: allow a bit more than qdrant operation timeout
    http_timeout = q_timeout + 10

    _request(
        cfg,
        "PUT",
        f"/collections/{cfg.collection}/points",
        params=params,
        payload=payload,
        timeout_s=http_timeout,
        retries=2,
        ok_status=(200, 201, 202),
    )


def search(
    cfg: QdrantConfig,
    *,
    vector: List[float],
    limit: int,
    qfilter: Optional[dict] = None,
    timeout: int = 30,
) -> dict:
    """
    Vector similarity search.

    POST /collections/{collection}/points/search
    Body includes:
      - vector: [...]
      - limit: N
      - filter: {...} (optional)
      - with_payload: true
      - with_vector: false
    """
    payload: Dict[str, Any] = {
        "vector": vector,
        "limit": int(limit),
        "with_payload": True,
        "with_vector": False,
    }
    if qfilter:
        payload["filter"] = qfilter

    r = _request(
        cfg,
        "POST",
        f"/collections/{cfg.collection}/points/search",
        payload=payload,
        timeout_s=max(5, int(timeout)) + 5,
        retries=2,
        ok_status=(200, 201, 202),
    )
    return _safe_json(r)


def count_points(cfg: QdrantConfig, *, qfilter: Optional[dict] = None) -> int:
    payload: Dict[str, Any] = {"exact": True}
    if qfilter:
        payload["filter"] = qfilter
    r = _request(
        cfg,
        "POST",
        f"/collections/{cfg.collection}/points/count",
        payload=payload,
        timeout_s=20,
        retries=1,
        ok_status=(200, 201, 202),
    )
    out = _safe_json(r)
    return int((out.get("result") or {}).get("count") or 0)


# ---------------------------------------------------------------------------
# Hybrid retrieval support (bge-m3: dense 1024d + sparse BM25-like vectors)
# Requires Qdrant >= 1.10 and named-vector collection format.
# ---------------------------------------------------------------------------


def create_collection_hybrid(
    cfg: QdrantConfig, *, dense_dim: int = 1024, distance: str = "Cosine"
) -> None:
    """Create a named-vector collection with dense + sparse vectors."""
    payload = {
        "vectors": {
            "dense": {"size": dense_dim, "distance": distance},
        },
        "sparse_vectors": {
            "sparse": {"index": {"type": "sparse", "on_disk": False}},
        },
    }
    _request(
        cfg,
        "PUT",
        f"/collections/{cfg.collection}",
        payload=payload,
        timeout_s=30,
        retries=1,
        ok_status=(200, 201, 202),
        allow_409=True,
    )


def ensure_collection_hybrid(
    cfg: QdrantConfig, *, dense_dim: int = 1024, distance: str = "Cosine"
) -> None:
    """Ensure a hybrid (named-vector) collection exists. Creates it if missing."""
    if not collection_exists(cfg):
        create_collection_hybrid(cfg, dense_dim=dense_dim, distance=distance)


def upsert_points_hybrid(
    cfg: QdrantConfig, points: List[dict], *, wait: bool = True, timeout: int = 60
) -> None:
    """
    Upsert points where each point's vector is a named-vector dict:
      {"dense": [float, ...], "sparse": {"indices": [int, ...], "values": [float, ...]}}
    """
    if not points:
        return
    q_timeout = max(1, int(timeout))
    params = {"wait": "true" if wait else "false", "timeout": str(q_timeout)}
    payload = {"points": points}
    http_timeout = q_timeout + 10
    _request(
        cfg,
        "PUT",
        f"/collections/{cfg.collection}/points",
        params=params,
        payload=payload,
        timeout_s=http_timeout,
        retries=2,
        ok_status=(200, 201, 202),
    )


def hybrid_query(
    cfg: QdrantConfig,
    *,
    dense_vector: List[float],
    sparse_indices: List[int],
    sparse_values: List[float],
    limit: int,
    qfilter: Optional[dict] = None,
    prefetch_limit: int = 30,
    timeout: int = 30,
) -> dict:
    """
    Hybrid retrieval via Qdrant /points/query (RRF fusion of dense + sparse).
    Requires Qdrant >= 1.10.

    Returns same structure as ``search``: {"result": [...hits...], ...}
    but hits come from res["result"]["points"] — normalised before return.
    """
    body: Dict[str, Any] = {
        "prefetch": [
            {
                "query": {"indices": sparse_indices, "values": sparse_values},
                "using": "sparse",
                "limit": prefetch_limit,
            },
            {
                "query": dense_vector,
                "using": "dense",
                "limit": prefetch_limit,
            },
        ],
        "query": {"fusion": "rrf"},
        "limit": limit,
        "with_payload": True,
        "with_vector": False,
    }
    if qfilter:
        body["filter"] = qfilter

    r = _request(
        cfg,
        "POST",
        f"/collections/{cfg.collection}/points/query",
        payload=body,
        timeout_s=max(5, int(timeout)) + 5,
        retries=2,
        ok_status=(200, 201, 202),
    )
    raw = _safe_json(r)
    # Normalise to same shape as ``search`` output: {"result": [hit, ...]}
    result = raw.get("result") or {}
    if isinstance(result, dict):
        # Qdrant 1.10+: {"result": {"points": [...]}}
        hits = result.get("points") or []
    else:
        # Fallback: already a list
        hits = result
    return {"result": hits}