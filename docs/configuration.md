# Configurazione — spiru-ops

## File di configurazione

Il sistema usa due livelli di configurazione:
1. **`.env`** — variabili d'ambiente: segreti, URL servizi, knob operativi
2. **`configs/*.yaml`** — configurazione dominio: focus tematici, domini, scoring

---

## `.env` — variabili d'ambiente

Il file `.env` non è in git (contiene API keys). Il template è in `.env.example`.
Deve avere permessi `600`:
```bash
chmod 600 .env
```

### API Keys
| Variabile | Obbligatoria | Descrizione |
|-----------|-------------|-------------|
| `BRAVE_API_KEY` | Raccomandata | Key per Brave Search API (discovery principale) |
| `OPENAI_API_KEY` | Richiesta per copilot | Key OpenAI per SpiruCopilot (RAG + LLM) |
| `UNPAYWALL_EMAIL` | Raccomandata | Email per Unpaywall API (OA PDF URLs via DOI) — gratuito, solo email |
| `CROSSREF_MAILTO` | Opzionale | Email per Crossref API — aumenta rate limit |

### Modelli e servizi
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `OPENAI_MODEL` | `gpt-5.2` | Modello OpenAI per il copilot |
| `OPENAI_DEEP_RESEARCH_MODEL` | `o3-deep-research` | Modello per ricerca profonda settimanale |
| `EMBED_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Modello embedding (384 dim) |
| `QDRANT_URL` | `http://localhost:6333` | URL Qdrant |
| `QDRANT_COLLECTION` | `docs_chunks` | Nome collection vettoriale |
| `UNSTRUCTURED_URL` | `http://localhost:8000` | URL Unstructured API |
| `GROBID_URL` | `http://localhost:8070` | URL Grobid |

### Grobid
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `GROBID_ENABLE` | `0` (profilo) | `1` = abilita Grobid per paper accademici |
| `GROBID_FULLTEXT` | `0` | `1` = estrazione fulltext (più lento, raramente utile) |

> **Nota**: In produzione `.env` forza `GROBID_ENABLE=1`, sovrascrivendo il default del profilo. Grobid viene quindi usato sempre in produzione.

### Storage
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `RAW_DIR` | `storage/raw` | Download originali |
| `PARSED_DIR` | `storage/parsed` | Testo estratto |
| `ARTIFACTS_DIR` | `storage/artifacts` | Report e aggregati |
| `STATE_DIR` | `storage/state` | Artefatti per-run (JSONL/JSON) |

### Pipeline knobs
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `PROFILE` | `balanced` | Profilo runtime. Cron usa `kb_first` |
| `MAX_CANDIDATES_PER_FOCUS` | `80` | Max candidati per area tematica in discovery |
| `MAX_TOTAL_CANDIDATES` | `800` | Cap globale candidati per run |
| `DISCOVERY_SINCE_DAYS` | `120` | Finestra temporale per query OpenAlex (giorni) |
| `USER_AGENT` | `spiru-ops-bot/0.3 (+...)` | User-Agent per download HTTP |
| `DENY_RESEARCHGATE` | `1` | Skippa ResearchGate (paywall duro) |
| `RESOLVE_DOI_REDIRECTS` | `1` | Risolve redirect DOI prima dell'ingest |
| `RESOLVE_DOI_TIMEOUT_S` | `8` | Timeout risoluzione redirect DOI |
| `DISCOVER_MAX_CAND_PER_DOMAIN` | `6` | Max candidati per dominio in discovery (round-robin) |

### Ingest
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `INGEST_TARGET` | `200` | Numero target documenti da ingestionare (portfolio selection) |
| `INGEST_MAX_PER_DOMAIN` | `10` | Max documenti per domain family in ingest |
| `INGEST_EXPLORATION_PCT` | `70%` | % di documenti da domini nuovi (vs exploitation) |
| `INGEST_HISTORY_DAYS` | `14` | Finestra storica per exploitation (run passati) |
| `MAX_DOWNLOAD_MB` | Profilo | Dimensione max file da scaricare (MB) |
| `UNSTRUCTURED_MAX_MB` | Profilo | Dimensione max per passare a Unstructured (sopra: pypdf) |

### Indexing
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `CHUNK_MAX_CHARS` | `2200` | Dimensione max chunk in caratteri |
| `CHUNK_OVERLAP` | `240` | Overlap tra chunk consecutivi |
| `INDEX_MIN_SPIRULINA_SCORE` | `0.25` | Score minimo per indicizzare un documento |
| `QDRANT_UPSERT_BATCH` | `64` | Dimensione batch upsert Qdrant |

### Reporting e copilot
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `TOP_K_PER_FOCUS` | `12` | Top N domini/focus nel report |
| `COPILOT_TOPK` | `10` | Top-K chunk da recuperare in RAG |
| `COPILOT_MAX_CONTEXT_CHARS` | `18000` | Max caratteri contesto per il LLM |

### QC thresholds
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `QC_MIN_CANDIDATES` | `200` | Min candidati per PASS |
| `QC_MIN_INDEXED_POINTS` | `200` | Min punti indicizzati in questo run per PASS |
| `QC_MAX_PENAL_SHARE` | `0.35` | Max % doc da domini penalizzati |
| `QC_MAX_MISSING_PUB_SHARE` | `0.60` | Max % doc senza anno pubblicazione |
| `QC_MIN_PREFER_SHARE` | `0.10` | Min % doc da sorgenti preferite |
| `QC_MAX_TOP5_DOMAIN_SHARE` | `0.70` | Max concentrazione top-5 domini |
| `QC_MIN_UNIQUE_DOMAINS` | `60` | Min domini unici (con floor dinamico) |
| `QC_MIN_UNIQUE_DOMAINS_SHARE` | `0.55` | Fattore floor dinamico unique_domains |
| `QC_MIN_SPIRULINA_SHARE` | `0.35` | Min % doc con score ≥ 0.50 |
| `QC_MIN_AVG_SPIRULINA_SCORE` | `0.28` | Min score Spirulina medio |

### OCR
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `OCR_LIMIT` | `5` | Max PDF da processare per run |
| `OCR_QUEUE` | `storage/backlog/ocr_queue.jsonl` | Coda OCR |
| `OCR_OUT_DIR` | `storage/ocr` | Output PDF OCR-izzati |

### Notifiche
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `NOTIFY_WEBHOOK_URL` | (vuoto) | URL webhook opzionale per notifica QC FAIL / retry failed. Se non configurato, le notifiche sono disabilitate |

### Pruning
| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `ARTIFACT_RETENTION_DAYS` | `30` | Giorni di retention file stato |
| `SEEN_URLS_MAX_LINES` | `15000` | Cap righe seen_urls.jsonl |

---

## `configs/focus.yaml` — aree tematiche

Definisce le 18 aree tematiche per la discovery. Ogni focus ha:
- `name`: identificatore snake_case usato in tutti gli artefatti
- `keywords`: termini chiave (usati da Qdrant scoring e relevance)
- `openalex_query`: stringa booleana per OpenAlex
- `brave_queries`: lista query Brave Search, una per variante

### Focus disponibili (produzione)

| Focus | Priorità (base_score) | Argomento |
|-------|----------------------|-----------|
| `process_control_setpoints_ph_co2_temp` | 30 | Controllo pH, CO₂, temperatura, bicarbonato |
| `pbr_airlift_geometry_and_scale_down` | 30 | Fotobioreattori airlift, kLa, gas holdup |
| `harvesting_fresh_biomass_filtration` | 25 | Raccolta biomassa, microstrainer, filtrazione |
| `certifications_protocols_food_cosmetic` | 25 | ISO 22000, HACCP, ISO 22716, GMP |
| `biomass_analytics_food_cosmetic_safety` | 25 | Sicurezza EFSA/FDA, metalli pesanti, microbiologico |
| `quality_shelf_life_storage_degradation` | 22 | Shelf life, stabilità, ossidazione, imballaggio |
| `illumination_led_commercial_roi` | 20 | LED spectrum, PPFD, efficienza fotonica |
| `packaging_labeling_retail` | 20 | Packaging, etichettatura, requisiti UE |
| `cip_cleaning_sanitation_material_compatibility` | 18 | CIP, biofilm, compatibilità materiali |
| `gas_management_o2_stripping_kla` | 18 | Degassing, stripping O₂, kLa |
| `marketing_branding_consumer_perception` | 18 | Brand, consumer acceptance, salute |
| `contamination_monitoring_response` | 16 | Contaminanti, rotiferi, protozoi, controllo |
| `spirulina_strains_eu_collections` | 15 | SAG, CCAP, DSMZ, UTEX, ceppi |
| `water_treatment_well_mains` | 15 | UV, RO, pretrattamento acqua |
| `circular_economy_waste_streams` | 15 | Digestato, acque reflue, economia circolare |
| `diy_home_cultivation_kits` | 15 | Coltivazione domestica, kit, hobbyist |
| `raceway_pond_design_operations` | — | Raceway pond, paddle wheel |
| `network_partners_accelerators_suppliers` | 10 | JRC, fornitori EU, scale-up |
| `local_adaptation_marche_fermo_outdoor` | 10 | Marche, outdoor, stagionale |
| `culture_parameters_and_media_cost` | — | Zarrouk medium, nutrienti, costo |
| `alternative_products_phyco_pigments_bioplastics` | — | Ficocianina, PHB, bioplastiche |

---

## `configs/domains.yaml` — domini

```yaml
deny_domains:
  - facebook.com
  - instagram.com
  - tiktok.com
  - pinterest.com

prefer_domains:       # +1 punto in scoring candidati; +1 in QC prefer_share
  - .edu
  - .ac.uk
  - .gov
  - europa.eu
  - efsa.europa.eu
  - fao.org
  - who.int
  - cnr.it
  - unibo.it
  - polimi.it

pdf_bonus_domains:    # bonus se il candidato è PDF da questi publisher
  - mdpi.com
  - frontiersin.org
  - elsevier.com
  - sciencedirect.com
  - springer.com
  - nature.com
  - wiley.com
  - tandfonline.com
```

---

## `configs/scoring.yaml` — pesi per Qdrant

Definisce per ogni focus una `base_score` e 3 query embedding di esempio. Usato da `discover.py` per assegnare il punteggio iniziale ai candidati e potenzialmente per il boosting in retrieval.

---

## `pipelines/profiles.sh` — profili runtime

Due profili pre-definiti. Il profilo viene scelto via `PROFILE` env var.

### `balanced` (default manuale)
- `MAX_DOWNLOAD_MB=50`, `UNSTRUCTURED_MAX_MB=25`
- Timeout: PDF 90s, HTML 40s, HEAD 20s
- Circuit breaker: 5 × 403, 3 × 429
- `GROBID_ENABLE=0`, `OPENALEX_ENRICH_ALWAYS=0`

### `kb_first` (produzione — cron)
- `MAX_DOWNLOAD_MB=120`, `UNSTRUCTURED_MAX_MB=15`
  - Scarica file più grandi, ma li passa a pypdf (non Unstructured) per sicurezza
- Timeout: PDF 120s, HTML 60s, HEAD 25s
- Circuit breaker: 15 × 403, 5 × 429 (più tollerante)
- `GROBID_ENABLE=0` (sovrascitto da `.env` che ha `GROBID_ENABLE=1`)
- `OPENALEX_ENRICH_ALWAYS=1` — arricchisce DOI tramite OpenAlex anche senza richiesta esplicita

> Le variabili nel profilo usano `${VAR:-default}`: se già definita in `.env`, il profilo non la sovrascrive. Il `.env` ha la precedenza.
