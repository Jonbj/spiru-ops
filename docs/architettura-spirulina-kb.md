# Architettura — Sistema di Knowledge Base Automatico per Spirulina

---

## 1. Architettura complessiva

Il sistema è composto da cinque macro-componenti che comunicano tramite filesystem locale e un database SQLite condiviso.

**A. Crawler/Discoverer** — Esegue query di ricerca su API e siti accademici, produce una lista di URL candidati con metadati grezzi. Non scarica ancora i documenti.

**B. Fetcher/Parser** — Prende gli URL candidati, scarica HTML e PDF, estrae il testo pulito (con OCR dove necessario), arricchisce i metadati bibliografici (DOI, autori, anno, journal). Salva il testo estratto e i metadati nel DB.

**C. Indexer** — Prende i documenti nuovi, li divide in chunk, genera embedding vettoriali e li inserisce nell'indice semantico. Gestisce anche la deduplicazione a livello di contenuto.

**D. QA Engine** — Riceve una domanda in linguaggio naturale, cerca i chunk più rilevanti nell'indice vettoriale, li passa a un LLM con un prompt RAG (Retrieval-Augmented Generation) e restituisce una risposta con citazioni.

**E. Scheduler/Orchestrator** — Coordina l'esecuzione ciclica di A→B→C, produce report, gestisce retry e cleanup.

```
┌──────────────┐
│  Scheduler   │
│ (cron/APSch) │
└──────┬───────┘
       │ lancia ciclo
       ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Discoverer  │───▶│   Fetcher/   │───▶│   Indexer     │
│  (search)    │    │   Parser     │    │  (embed+store)│
└──────────────┘    └──────────────┘    └───────┬───────┘
                                                │
                                        ┌───────▼───────┐
                                        │  Vector Store  │
                                        │  (ChromaDB)    │
                                        └───────┬───────┘
                                                │
                                        ┌───────▼───────┐
                                        │   QA Engine    │◀── utente (CLI/web)
                                        │   (RAG+LLM)   │
                                        └───────────────┘
```

Tutti i dati persistenti vivono in una singola directory `data/` con sottodirectory: `data/db/` (SQLite), `data/chroma/` (indice vettoriale), `data/logs/`, `data/reports/`.

---

## 2. Stack tecnologico

| Componente | Scelta | Motivazione |
|---|---|---|
| **Linguaggio** | Python 3.11+ | Ecosistema ML/NLP maturo, librerie per PDF e scraping, unico linguaggio per tutto |
| **Database metadati** | SQLite (via `sqlite3` stdlib) | Zero dipendenze esterne, file singolo, perfetto per single-machine, supporta WAL per letture concorrenti |
| **Indice vettoriale** | ChromaDB (embedded mode) | Gira in-process senza server separato, persiste su disco, supporta metadati filtrabili, API semplice |
| **Embedding** | `sentence-transformers` con modello `multilingual-e5-base` | Gira localmente senza API esterna, buona qualità per retrieval semantico multilingue (IT/EN/FR/ES) |
| **LLM per QA** | API Anthropic (Claude) oppure OpenAI, configurabile via env var | Qualità delle risposte RAG molto superiore ai modelli locali piccoli; il costo per uso personale è trascurabile |
| **Estrazione PDF** | `pymupdf` (fitz) + `pytesseract` per OCR | pymupdf è veloce e gestisce bene i PDF accademici; Tesseract copre i PDF scansionati |
| **Estrazione HTML** | `trafilatura` | Specializzata nell'estrazione di contenuto da pagine web, rimuove boilerplate, gestisce multilingue |
| **Ricerca fonti** | API Semantic Scholar, CrossRef, CORE, Google Scholar (via `scholarly`) | Copertura complementare: Semantic Scholar per paper CS/bio, CrossRef per DOI/metadati, CORE per open-access EU |
| **Scheduling** | `APScheduler` (in-process) oppure cron di sistema | APScheduler permette scheduling programmatico con retry integrato; cron è l'alternativa robusta |
| **CLI/Web** | CLI con `click` + opzionalmente FastAPI per interfaccia web minimale | CLI per uso rapido; FastAPI aggiunge interfaccia browser senza overhead |
| **Logging** | `logging` stdlib → file + console | Standard, zero dipendenze |

---

## 3. Flusso dati end-to-end

### Ciclo di raccolta (eseguito N volte al giorno)

1. Lo **Scheduler** avvia un ciclo. Scrive un record `run` nel DB con timestamp e stato `running`.

2. Il **Discoverer** itera sulle aree tematiche configurate. Per ciascuna, costruisce query multilingue (es. `"spirulina photobioreactor airlift"`, `"Spirulina fotobioreattore aerazione"`, `"spiruline photobioréacteur"`) e le invia alle API di ricerca. Raccoglie URL, titoli, abstract, DOI quando disponibili. Scrive ogni candidato nella tabella `candidates` del DB con stato `new`.

3. Il **Deduplicator** filtra i candidati: scarta quelli il cui URL o DOI è già presente nella tabella `documents` (documenti già acquisiti). Aggiorna lo stato dei candidati scartati a `duplicate_url`.

4. Il **Fetcher** prende i candidati non duplicati e scarica il contenuto. Per ogni URL:
   - Tenta il download con timeout e retry (max 3 tentativi, backoff esponenziale).
   - Identifica il tipo di contenuto (HTML o PDF).
   - Per HTML, usa `trafilatura` per estrarre il testo.
   - Per PDF, usa `pymupdf` per estrarre testo; se il testo è sotto una soglia minima (PDF scansionato), attiva OCR con Tesseract.
   - Salva il testo pulito nella tabella `documents`.
   - Arricchisce i metadati via CrossRef (lookup DOI → autori, anno, journal).

5. Il **Deduplicator contenuto** calcola un hash (SHA-256) del testo normalizzato di ogni nuovo documento. Se l'hash è già presente nel DB, marca il documento come `duplicate_content` e non procede all'indicizzazione.

6. L'**Indexer** prende i documenti nuovi e non duplicati. Li divide in chunk (800 token con 100 token di overlap, divisione per paragrafi quando possibile). Genera embedding per ogni chunk con il modello locale. Inserisce i chunk con embedding e metadati in ChromaDB.

7. Il **Quality Checker** verifica:
   - Numero di nuovi documenti (warning se zero).
   - Distribuzione dei domini (warning se >60% da un singolo dominio).
   - Pertinenza (verifica che il testo contenga termini legati a Spirulina/microalghe).
   - Rapporto segnale/rumore (lunghezza testo vs lunghezza attesa).

8. Il **Reporter** genera un report Markdown con statistiche del ciclo e lo salva in `data/reports/`. Aggiorna il record `run` nel DB con stato `completed` e statistiche aggregate.

### Flusso di interrogazione (on-demand)

1. L'utente pone una domanda (CLI o web).
2. Il sistema genera l'embedding della domanda.
3. ChromaDB restituisce i top-K chunk più simili (K=10–15), con score e metadati.
4. I chunk vengono assemblati in un prompt RAG con istruzioni precise: rispondi solo basandoti sui passaggi forniti, cita le fonti, dichiara esplicitamente se l'informazione non è presente.
5. Il LLM genera la risposta.
6. Il sistema formatta la risposta con citazioni (titolo, autori, anno, DOI/URL per ogni fonte usata).

---

## 4. Schema dati

### SQLite — Tabella `runs`

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `started_at` | TEXT | ISO 8601 |
| `completed_at` | TEXT | ISO 8601 |
| `status` | TEXT | `running` · `completed` · `failed` |
| `docs_found` | INTEGER | Candidati trovati |
| `docs_fetched` | INTEGER | Documenti scaricati con successo |
| `docs_indexed` | INTEGER | Documenti indicizzati |
| `docs_duplicate` | INTEGER | Duplicati scartati |
| `errors` | INTEGER | Errori totali nel ciclo |
| `report_path` | TEXT | Path del report generato |

### SQLite — Tabella `candidates`

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `run_id` | INTEGER | FK → `runs` |
| `url` | TEXT | URL originale |
| `title` | TEXT | Titolo dal risultato di ricerca |
| `abstract` | TEXT | Abstract se disponibile |
| `doi` | TEXT | DOI se disponibile |
| `source_api` | TEXT | `semantic_scholar` · `crossref` · `core` · `scholarly` |
| `theme` | TEXT | Area tematica della query |
| `language` | TEXT | Lingua della query |
| `status` | TEXT | `new` · `fetched` · `duplicate_url` · `duplicate_content` · `failed` |
| `created_at` | TEXT | ISO 8601 |

### SQLite — Tabella `documents`

| Campo | Tipo | Note |
|---|---|---|
| `id` | INTEGER PRIMARY KEY | Auto-increment |
| `candidate_id` | INTEGER | FK → `candidates` |
| `url` | TEXT UNIQUE | URL normalizzato |
| `doi` | TEXT | DOI |
| `content_hash` | TEXT | SHA-256 del testo normalizzato |
| `title` | TEXT | Titolo arricchito |
| `authors` | TEXT | JSON array |
| `year` | INTEGER | Anno di pubblicazione |
| `journal` | TEXT | Nome del journal |
| `language` | TEXT | Lingua rilevata del testo |
| `source_type` | TEXT | `pdf` · `html` |
| `text_length` | INTEGER | Lunghezza testo estratto in caratteri |
| `chunk_count` | INTEGER | Numero di chunk generati |
| `fetched_at` | TEXT | ISO 8601 |
| `indexed_at` | TEXT | ISO 8601 |

### ChromaDB — Collection `spirulina_kb`

Ogni record (chunk) contiene:

| Campo | Tipo | Note |
|---|---|---|
| `id` | string | Formato `{document_id}_{chunk_index}` |
| `embedding` | float[384] | Dimensione dipendente dal modello scelto |
| `document` | string | Testo del chunk |
| `metadata` | object | `doc_id`, `title`, `authors`, `year`, `doi`, `url`, `theme`, `language`, `chunk_index`, `total_chunks` |

---

## 5. Strategia di scheduling e robustezza

### Scheduling

APScheduler configurato con un `IntervalTrigger` (es. ogni 8 ore = 3 volte al giorno). Il job è il ciclo completo Discover → Fetch → Index → QA Check → Report. L'intervallo è configurabile via file `config.yaml`.

### Robustezza

- Ogni fase del ciclo è wrappata in try/except. Un fallimento in un singolo documento non interrompe il ciclo — viene loggato e il documento marcato come `failed`.
- Il Fetcher usa timeout (30s per HTML, 120s per PDF) e retry con backoff esponenziale (3 tentativi).
- Un file di lock (`data/.lock`) impedisce l'esecuzione concorrente di due cicli.
- Se il processo viene killato, al riavvio il lock stale viene rilevato (il PID nel lock file non esiste più) e rimosso.

### Cleanup

A fine ciclo vengono rimossi i file temporanei (PDF scaricati per OCR, HTML grezzi). I dati permanenti (SQLite, ChromaDB, report, log) non vengono toccati. I log più vecchi di 30 giorni vengono compressi e archiviati.

### Configurazione

Un file `config.yaml` alla radice del progetto contiene tutte le impostazioni (intervallo, riferimenti a env vars per le API keys, temi di ricerca, soglie QA). Le API keys effettive vivono in variabili d'ambiente o in un file `.env` (escluso dal versioning).

---

## 6. Strategia di deduplicazione

Tre livelli complementari:

1. **URL normalizzato** — Prima del fetch, l'URL viene normalizzato (lowercased, parametri tracking rimossi, trailing slash rimosso). Se esiste già in `documents`, il candidato è scartato.

2. **DOI** — Se il candidato ha un DOI e un documento con lo stesso DOI esiste già, il candidato è scartato. Questo cattura lo stesso paper raggiunto da URL diversi (es. versione publisher vs preprint).

3. **Content hash** — Dopo l'estrazione del testo, viene calcolato SHA-256 sul testo normalizzato (lowercase, spazi collassati, punteggiatura rimossa). Questo cattura duplicati con URL e DOI diversi ma contenuto identico (es. mirror, cache istituzionali).

---

## 7. Interfaccia utente

### CLI (primaria)

```bash
# Interrogazione
spirulina-kb ask "Quale pH ottimale per Spirulina in un PBR da 50L?"

# Lancio manuale di un ciclo
spirulina-kb collect --run-now

# Stato del sistema
spirulina-kb status

# Ultimo report
spirulina-kb report --latest

# Statistiche knowledge base
spirulina-kb stats
```

La risposta del comando `ask` mostra: la risposta del LLM, seguita da una sezione **Fonti** con titolo, autori, anno e link per ogni documento citato. Mostra anche uno score di confidenza basato sulla similarità media dei chunk recuperati.

### Web (opzionale, fase 2)

Una pagina FastAPI minimale con un campo di ricerca e visualizzazione delle risposte formattate. Utile per navigare i report e le statistiche.

---

## 8. Punti critici e rischi

| Rischio | Impatto | Mitigazione |
|---|---|---|
| **Rate limiting delle API di ricerca** | Il Discoverer non trova abbastanza fonti | Ruotare tra più API (Semantic Scholar, CORE, CrossRef), rispettare rate limits con sleep, cache delle query già fatte |
| **PDF protetti o corrotti** | Fetch fallisce o testo estratto è vuoto | Fallback OCR, log esplicito, il ciclo continua comunque |
| **Qualità embedding multilingue** | Retrieval scarso su documenti in lingue miste | Usare un modello esplicitamente multilingue (`multilingual-e5-base`); testare manualmente nelle prime settimane |
| **Deriva tematica** | Il sistema indicizza documenti irrilevanti | Il Quality Checker verifica la pertinenza; soglia minima di keyword Spirulina/microalgae nel testo |
| **Costo API LLM** | Costi per il QA engine | Il LLM è usato solo nella fase di interrogazione (non nel ciclo di raccolta); costo nell'ordine di pochi euro/mese |
| **Spazio disco** | ChromaDB cresce nel tempo | Monitoraggio nella fase di status; ~2KB per chunk; 10.000 documenti ≈ qualche centinaio di MB |
| **Google Scholar blocking** | La libreria `scholarly` viene bloccata frequentemente | Usarla come fonte secondaria; privilegiare API ufficiali con chiave (Semantic Scholar, CORE) |

---

## 9. Fuori dallo scope iniziale (v2+)

- **Aggiornamento incrementale dell'indice vettoriale** — Nella v1, se si vuole ricostruire l'indice si rifà da zero. L'aggiornamento granulare (rimuovere chunk di documenti ritirati) è complessità da aggiungere dopo.

- **Web UI completa** — La v1 è CLI-only. La web UI è un'aggiunta successiva con FastAPI.

- **OCR avanzato** — Tesseract copre i casi base. Per PDF accademici molto complessi (tabelle, formule), un parser come GROBID sarebbe superiore, ma è un servizio Java separato che aggiunge complessità infrastrutturale.

- **Ranking sofisticato delle fonti** — Nella v1, il retrieval è puramente per similarità coseno. Un re-ranker cross-encoder (es. `cross-encoder/ms-marco-MiniLM-L-6-v2`) migliorerebbe la precisione ma aggiunge latenza.

- **Alerting** — Nella v1, i report sono file. Notifiche email/Telegram quando un ciclo fallisce sono utili ma non critiche.

- **Gestione versioni dei documenti** — Se un paper viene aggiornato, la v1 lo tratta come documento diverso (hash diverso). La gestione esplicita delle versioni è complessità prematura.

- **Full-text search keyword** — La v1 usa solo ricerca semantica. Un indice FTS complementare (SQLite FTS5) è utile per query precise (es. cercare un DOI specifico) e va aggiunto in v2.
