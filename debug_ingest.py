import json
import pathlib
from collections import Counter
from urllib.parse import urlparse
from datetime import datetime, timezone
from pipelines.common import normalize_url, load_domains, denied
from pipelines.ingest import _domain, _domain_family, load_seen

# Load seen
seen_path = pathlib.Path("storage/state/seen_urls.jsonl")
seen = load_seen(seen_path)

seen_doi_path = pathlib.Path("storage/state/seen_doi.jsonl")
seen_doi = set()
if seen_doi_path.exists():
    for line in seen_doi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            d = (obj.get("doi") or "").strip().lower()
            if d:
                seen_doi.add(d)
        except Exception:
            pass

# Load domain fail cache
domain_403 = {}
domain_429 = {}
cache_path = pathlib.Path("storage/state/domain_fail_cache.json")
_now_ts = datetime.now(timezone.utc).timestamp()
_TTL_403 = 7 * 86400
_TTL_429 = 6 * 3600
if cache_path.exists():
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        for dom_key, entry in cache.get("domain_403", {}).items():
            if _now_ts - entry.get("ts", 0) < _TTL_403:
                domain_403[dom_key] = entry.get("count", 1)
        for dom_key, entry in cache.get("domain_429", {}).items():
            if _now_ts - entry.get("ts", 0) < _TTL_429:
                domain_429[dom_key] = entry.get("count", 1)
    except Exception:
        pass

dom_cfg = load_domains("configs/domains.yaml")

# Load candidates
items = []
with open("storage/state/2026-05-11T001001Z_candidates.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            pass

# Apply prefilters (seen + hint)
_EXEMPT = {"openalex","semantic_scholar","core","manual_seed","strain_seed"}
cands = []
for c in items:
    url = normalize_url(c.get("url", ""))
    if url and url not in seen:
        src = c.get("source","")
        hint = float(c.get("spirulina_hint") or 0.0)
        if src in _EXEMPT or hint > 0.0:
            cands.append(c)

print(f"After prefilters: {len(cands)} candidates")

# Simulate loop filters (no download)
skipped = Counter()
failures = Counter()
would_download = []

for it in cands:
    url = normalize_url(it.get("url", ""))
    dom = _domain(url)
    
    if domain_403.get(dom, 0) >= 5:
        skipped["domain_403_cooldown"] += 1
        continue
    if domain_429.get(dom, 0) >= 3:
        skipped["domain_429_cooldown"] += 1
        continue
    if url in seen:
        skipped["already_seen"] += 1
        continue
    if denied(url, dom_cfg.get("deny_domains", [])):
        skipped["denied_domain"] += 1
        continue
    
    doi = (it.get("doi") or "").strip() or None
    if doi:
        doi_norm = doi.lower()
        if doi_norm in seen_doi:
            skipped["already_seen_doi"] += 1
            continue
    
    would_download.append(it)

print(f"Would download: {len(would_download)}")
print(f"Skipped: {dict(skipped)}")
