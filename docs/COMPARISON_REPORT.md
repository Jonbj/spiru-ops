# Report comparativo — architetture AI vs progetto attuale

> Generato il 2026-03-07. Confronta due proposte architetturali ("Doc A" e "Doc B") generate da modelli AI a partire dal reverse engineering prompt, con il progetto spiru-ops attuale.

---

## 1. Panoramica dei tre sistemi

| Dimensione | Progetto attuale | Doc A (architettura_kb_spirulina) | Doc B (architettura-spirulina-kb) |
|---|---|---|---|
| **Discovery** | Brave Search + OpenAlex | OpenAlex + Crossref + query web su domini autorevoli | Semantic Scholar + CrossRef + CORE + Google Scholar |
| **Scheduling** | cron di sistema | systemd timer/service | APScheduler in-process + cron |
| **Metadata store** | JSONL flat files | SQLite + FTS5 | SQLite |
| **Parsing HTML** | BeautifulSoup custom (`soup_text`) | Docling + PyMuPDF | trafilatura |
| **Parsing PDF** | Unstructured API (Docker) + pypdf | Docling + PyMuPDF + Grobid + Tesseract | pymupdf + Tesseract |
| **Embedding** | `all-MiniLM-L6-v2` (384d, EN-centrico) | SBERT multilingue (non specificato) | `multilingual-e5-base` (768d) |
| **Vector store** | Qdrant (Docker) | Qdrant | ChromaDB (embedded) |
| **Retrieval** | Solo vettoriale | Ibrida: vettoriale + BM25/FTS5 + reranking | Solo vettoriale |
| **UI** | Streamlit | FastAPI + 3 schermate (Ask, Library, Runs) | CLI (click) + FastAPI opzionale |
| **Dedup** | URL + DOI + content_hash | 4 livelli: URL + DOI + hash binario + hash testuale + SimHash | 3 livelli: URL + DOI + content_hash |
| **Schema dati** | JSONL + `.meta.json` per file | Relazionale: `documents`, `chunks`, `runs`, `candidates`, `run_events`, `query_logs` | Relazionale: `documents`, `candidates`, `runs` in SQLite |

---

## 2. Cosa i due nuovi progetti propongono di meglio

### 2.1 Modello embedding multilingue ⭐⭐⭐ (impatto alto)

**Cosa propongono**: entrambi scelgono un modello embedding esplicitamente multilingue.
- Doc A: SBERT multilingue (non specificato esattamente)
- Doc B: `multilingual-e5-base` — produce embedding comparabili tra lingue nello stesso spazio vettoriale

**Problema nell'attuale**: `all-MiniLM-L6-v2` è addestrato principalmente su corpus inglese. Il progetto raccoglie documenti in italiano, francese, spagnolo (le query Brave sono esplicitamente multilingue, `configs/focus.yaml` ha query IT/FR/ES). Un documento in italiano indicizzato con questo modello produce embedding di qualità inferiore. Conseguenza: una query in italiano non recupera bene documenti in italiano, e viceversa.

**Applicabilità al progetto attuale**: Alta, ma con costo non banale.
- Cambiare il modello in `.env`: `EMBED_MODEL=intfloat/multilingual-e5-base`
- ⚠️ Il modello ha dimensione 768 invece di 384 → la collection Qdrant `docs_chunks` va ricreata (dimensione vettore diversa)
- ⚠️ Tutti i ~67.000 punti vanno ri-embeddati → serve un run di re-index completo su tutto `storage/parsed/`
- Il modello `multilingual-e5-base` richiede prefisso `"query: "` e `"passage: "` nei testi — va adattato `index.py` e `rag_cloud.py`

---

### 2.2 SQLite come metadata store ⭐⭐⭐ (impatto alto)

**Cosa propongono**: entrambi propongono SQLite per tutti i metadati, con schema relazionale esplicito (tabelle `documents`, `candidates`, `runs`, `run_events`, `query_logs`).

**Problema nell'attuale**: lo stato è distribuito su decine di file JSONL e JSON:
- `{RUN_ID}_candidates.jsonl` — una riga per URL candidato
- `{RUN_ID}_ingested.json` — sommario con lista embedded
- `seen_urls.jsonl` — stato globale URL visti
- `seen_doi.jsonl` — stato globale DOI visti
- `.meta.json` per ogni documento

Questo approccio ha limiti reali:
- **Dedup lookup in O(n)**: per sapere se un URL è già visto, `ingest.py` carica tutta `seen_urls.jsonl` in memoria
- **Nessuna query cross-run**: impossibile chiedersi "quante volte abbiamo visto documenti da `springer.com` nell'ultimo mese?"
- **Audit trail fragile**: non c'è un record strutturato del perché un documento è stato scartato
- **Rischio corruzione**: su scritture concorrenti o crash a metà, un JSONL parzialmente scritto è difficile da recuperare

**Con SQLite**:
- Lookup URL/DOI in O(log n) via indice
- Query arbitrarie su runs, domini, failure reasons
- Transazioni ACID — nessun rischio di file corrotti
- Un solo file da backuppare

**Applicabilità al progetto attuale**: Media-alta. È un refactor significativo di `ingest.py`, `discover.py`, `evaluate.py`, `kb_validate.py` — ma la logica di business resta la stessa.

---

### 2.3 Retrieval ibrida: vettoriale + BM25/FTS ⭐⭐ (impatto medio-alto, Doc A)

**Cosa propone Doc A**: oltre alla ricerca vettoriale (similarità semantica), aggiunge un indice full-text (SQLite FTS5 o BM25) e un reranker cross-encoder nella seconda fase.

**Problema nell'attuale**: la ricerca è solo vettoriale. Questo è un limite reale per:
- **Termini tecnici esatti**: query come `"kLa"`, `"ISO 22716"`, `"HACCP"` o un DOI specifico — termini rari o acronimi che il modello embedding conosce poco. Il vettoriale li "appiattisce" semanticamente.
- **Nomi propri**: `"Zarrouk medium"`, `"arthrospira platensis SAG 21.99"` — un FTS li troverebbe perfettamente, il vettoriale li cerca approssimativamente.

**La retrieval ibrida** (vettoriale + BM25, poi reranking) è considerata best-practice nei sistemi RAG attuali. Il reranker cross-encoder rilegge la coppia (query, chunk) insieme — molto più preciso del cosine similarity ma più lento.

**Applicabilità al progetto attuale**: Media. Qdrant supporta nativo il filtro payload (già usato per `focus`). Per BM25 si potrebbe aggiungere SQLite FTS5 e fare fusion dei risultati in `rag_cloud.py`. Il reranker richiederebbe un modello aggiuntivo (es. `cross-encoder/ms-marco-MiniLM-L-6-v2`).

---

### 2.4 trafilatura per HTML extraction ⭐⭐ (impatto medio, Doc B)

**Cosa propone Doc B**: `trafilatura` invece di BeautifulSoup custom per l'estrazione di contenuto da pagine web.

**Problema nell'attuale**: `common.soup_text()` è un'implementazione custom che rimuove script/style/nav/footer e applica euristiche per le righe boilerplate. Funziona, ma:
- Non gestisce bene layout a multi-colonna
- Non riconosce automaticamente il "corpo principale" di un articolo su un sito publisher complesso
- Può lasciare molto boilerplate da siti come ResearchGate, Academia.edu, pagine di landing editoriali

**trafilatura** è una libreria specializzata nell'estrazione di contenuto da pagine web. Usa un algoritmo di classificazione per identificare il contenuto principale della pagina, gestisce natively JavaScript-rendered content (con fallback), e produce output più pulito su siti publisher complessi.

**Applicabilità al progetto attuale**: Alta, basso rischio. Si tratterebbe di aggiungere `trafilatura` a `requirements.txt` e cambiare la chiamata in `ingest.py` per le URL HTML. Il fallback BeautifulSoup può restare se trafilatura restituisce testo vuoto. Non richiede Docker, gira in-process.

---

### 2.5 CORE API come fonte di discovery aggiuntiva ⭐⭐ (impatto medio, Doc B)

**Cosa propone Doc B**: aggiunge CORE (`core.ac.uk`) come fonte di discovery accanto a OpenAlex.

**Cos'è CORE**: è il più grande aggregatore europeo di open-access papers. Indicizza oltre 200 milioni di documenti da repository istituzionali, università, enti di ricerca UE. Ha un'API gratuita (con key) e copre molto bene documenti di origine europea — pertinente dato il focus del progetto su normativa EU (EFSA, FAO, CNR, università italiane).

**Differenza con OpenAlex**: OpenAlex è eccellente per metadati e citation graph ma non fornisce sempre il full-text. CORE invece è focalizzato sull'accesso al PDF open-access e al full-text. Per paper italiani/europei, CORE ha una copertura superiore.

**Applicabilità al progetto attuale**: Alta, addizionale. Si tratterebbe di aggiungere un canale in `discover.py` simile a quello OpenAlex già esistente.

---

### 2.6 Schema dati strutturato: `doc_type` + scoring multi-dimensionale ⭐ (impatto basso-medio, Doc A)

**Cosa propone Doc A**: classificare ogni documento per tipo (`paper`, `guideline`, `thesis`, `manual`, `regulation`, `webpage`) e assegnare ai candidati uno score multi-dimensionale: `source_authority_score`, `topic_match_score`, `language_score`, `document_type_score`, `freshness_score`, `open_access_likelihood`.

**Problema nell'attuale**: tutti i documenti sono trattati uniformemente. Una tesi magistrale, un regolamento EFSA e un blog post ricevono lo stesso trattamento di chunking, embedding e retrieval. In retrieval, non si può facilmente filtrare per "voglio solo linee guida ufficiali" o "voglio solo paper peer-reviewed degli ultimi 5 anni".

**Applicabilità**: Media. Il `doc_type` si può inferire dall'URL e dal dominio (es. `doi.org` → paper, `efsa.europa.eu` → regulation, `hdl.handle.net` → thesis). Il filtro per tipo in Qdrant è già supportato via payload filtering.

---

### 2.7 Query log per miglioramento iterativo ⭐ (impatto medio, Doc A)

**Cosa propone Doc A**: tabella `query_logs` con `question`, `chunk_ids_retrieved`, `answer_status`, `latency_ms`.

**Problema nell'attuale**: non c'è nessuna traccia delle domande fatte al copilot. Non è possibile sapere quali domande ricevono risposta "TBD" (perché il KB non ha copertura) e quindi non è possibile targetizzare la discovery su quegli argomenti.

**Applicabilità**: Alta e semplice. È un'aggiunta in `rag_cloud.py` + eventuale SQLite table.

---

## 3. Cosa il progetto attuale ha che i due nuovi non propongono

È importante notare che i nuovi sistemi, pur avendo idee migliori su alcuni punti, mancano di funzionalità già mature nel progetto attuale:

| Funzionalità attuale | Assente in Doc A | Assente in Doc B | Nota |
|---|---|---|---|
| **Portfolio selection** (exploration/exploitation, cap per domain family) | ✗ | ✗ | Algoritmo sofisticato per diversità degli ingest — non menzionato in nessuno dei due |
| **Circuit breaker 403/429 per dominio** | ✗ (menzionato vagamente come "cooldown") | ✗ | Il progetto attuale smette di provare un dominio dopo N errori nello stesso run |
| **RUN_ID stabile** (midnight-split protection) | ✗ | ✗ | Nessuno dei due affronta il problema del midnight split |
| **Profili runtime** (balanced / kb_first) | ✗ | ✗ | Configurazione per ambienti diversi |
| **Scoring Spirulina con penalità confounders** | Propone classificatore generico | Propone keyword matching | Il progetto ha un sistema pesato con penalità per alghe non-Spirulina |
| **trap EXIT → docker compose down** | ✗ | ✗ | Garanzia che i container si fermino anche su errore |
| **flock per concorrenza** | Lock file menzionato | Lock file menzionato | Il progetto usa `flock -n` (robusto) entrambi usano file lock a livello applicativo |
| **Unpaywall già integrato** | Menzionato come source secondaria | ✗ | Il progetto lo usa già in `enrich_doi_oa.py` |

---

## 4. Cosa non adottare dai nuovi progetti

### ChromaDB (Doc B)
Doc B propone ChromaDB "embedded" invece di Qdrant per semplicità (niente Docker). Qdrant è superiore per:
- Filtri payload più potenti e indicizzati
- Performance su collezioni grandi
- Supporto nativo per retrieval ibrida (sparse + dense vectors)
- API REST stabile e documentata

Non c'è motivo di cambiare.

### APScheduler in-process (Doc B)
Doc B propone APScheduler per scheduling dentro l'applicazione Python. Il cron di sistema è più robusto: sopravvive ai crash del processo Python, è monitorabile con strumenti OS standard, ha logging integrato via journalctl/syslog. La scelta attuale (cron) è corretta.

### systemd service (Doc A)
Doc A propone systemd per la gestione del servizio. È un miglioramento rispetto al cron solo se si vuole restart automatico, watchdog, e integrazione con journald. Per un sistema single-user su macchina dedicata, il cron con flock è sufficiente e meno invasivo.

### FastAPI (entrambi)
Entrambi propongono FastAPI per l'interfaccia. Streamlit è più adatta per un uso personale a sessioni occasionali (nessun server sempre up, interfaccia RAG già funzionante). FastAPI avrebbe senso solo se si volesse esporre il KB a più utenti o integrarlo in altri sistemi.

---

## 5. Riepilogo migliorie raccomandate — ordine di priorità

### Priorità 1 — Alto impatto, applicabile senza riscrivere tutto

| # | Miglioria | Effort | Rischio | Beneficio |
|---|-----------|--------|---------|-----------|
| 1 | **Modello embedding multilingue** (`multilingual-e5-base` o `paraphrase-multilingual-mpnet-base-v2`) | Medio (richiede re-index completo) | Medio (vettori incompatibili → collection da ricreare) | Alto — retrieval molto migliorato su doc non inglesi |
| 2 | **trafilatura per HTML extraction** (in `ingest.py`) | Basso | Basso | Medio — testo più pulito da siti publisher |
| 3 | **CORE API in `discover.py`** | Basso | Basso | Medio — più copertura paper europei/italiani |
| 4 | **Query log** in `rag_cloud.py` | Basso | Basso | Medio — consente di targettizzare discovery su gap del KB |

### Priorità 2 — Medio impatto, richiedono refactor

| # | Miglioria | Effort | Rischio | Beneficio |
|---|-----------|--------|---------|-----------|
| 5 | **SQLite come metadata store** (rimpiazza JSONL/JSON state files) | Alto (refactor di ingest, discover, evaluate, kb_validate) | Medio | Alto — auditabilità, query, dedup O(log n), robustezza |
| 6 | **Retrieval ibrida BM25 + vettoriale** in `rag_cloud.py` e `query.py` | Medio | Basso | Medio-alto — retrieval migliore per termini tecnici esatti |
| 7 | **Classificazione `doc_type`** (paper/guideline/thesis/regulation) | Basso-medio | Basso | Medio — filtri più precisi nel copilot |

### Priorità 3 — Complessità alta, beneficio marginale nell'immediato

| # | Miglioria | Note |
|---|-----------|------|
| 8 | **Reranker cross-encoder** | Migliora retrieval ma aggiunge latenza e dipendenza |
| 9 | **Scoring multi-dimensionale candidati** | L'attuale scoring funziona; affinamento incrementale |
| 10 | **Document versioning** | Complessità alta per un caso d'uso raro |
| 11 | **MinHash near-duplicate detection** | L'attuale dedup hash è sufficiente per lo scope |

---

## 6. Raccomandazione operativa

Se si vuole migliorare il progetto senza un refactor completo, la sequenza consigliata è:

1. **Cambio modello embedding** (Priorità 1.1) — il beneficio più immediato sulla qualità del RAG. Accettare il costo di un re-index completo.
2. **trafilatura** (Priorità 1.2) — due ore di lavoro, nessun rischio.
3. **CORE API** (Priorità 1.3) — una mattina, espande la copertura europea.
4. **Query log** (Priorità 1.4) — un pomeriggio, abilita il miglioramento continuo.

Dopo 1–3 mesi di uso, se il bottleneck diventa la qualità del retrieval su termini tecnici precisi, valutare la **retrieval ibrida** (Priorità 2.6).

Il **refactor a SQLite** (Priorità 2.5) è architetturalmente corretto ed è la scelta giusta se si prevede di far crescere il sistema nel tempo — ma richiede una settimana di lavoro pulito e non è urgente finché il volume di runs resta gestibile (~4 run/giorno × 30 giorni retention).
