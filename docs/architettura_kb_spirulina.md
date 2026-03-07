# Architettura proposta — Sistema di knowledge base automatico per Spirulina

Perfetto. Ti propongo un **MVP solido, locale, idempotente e diagnostico**, pensato per partire bene su una singola macchina e crescere senza dover riscrivere tutto.

## 1. Architettura complessiva

La dividerei in 7 componenti, separati ma tutti nello stesso repository/process space locale.

### A. Scheduler / Orchestrator
Responsabile di lanciare i cicli ricorrenti, applicare lock, gestire retry, timeout, cleanup e generare il report finale.

### B. Source Discovery
Cerca nuove fonti per tema e lingua. Non scarica ancora tutto: produce una lista di “candidate records” con URL, dominio, lingua stimata, area tematica, tipo fonte, priorità.

Sottocanali:
- discovery scientifico: OpenAlex, Crossref, query web mirate verso repository open-access e siti istituzionali
- discovery normativo/istituzionale: EFSA, FDA, FAO, WHO, enti nazionali, standard e linee guida
- discovery manualistica / small-scale: query web multilingue su domini autorevoli

### C. Acquisition
Scarica l’asset migliore disponibile:
- HTML canonico
- PDF publisher
- PDF alternativo open-access quando disponibile
- eventuali allegati rilevanti

Qui avvengono:
- content-type sniffing
- checksum binario
- naming stabile
- salvataggio artefatti grezzi

### D. Parsing & Enrichment
Trasforma il documento in testo pulito e strutturato:
- boilerplate removal HTML
- parsing PDF
- OCR solo quando serve
- estrazione sezioni, tabelle, riferimenti
- arricchimento bibliografico: DOI, autori, anno, journal, fonte, URL canonical, URL OA alternativo

### E. Quality & Dedup
Valuta:
- pertinenza a Spirulina / Arthrospira / Limnospira
- rumore del contenuto
- duplicati URL / DOI / contenuto
- diversità fonti
- copertura tematica del ciclo

Non ferma il run: marca, declassa o scarta secondo policy.

### F. Indexing & Retrieval
Produce:
- chunk semantici
- embedding multilingue
- indice vettoriale
- metadati filtrabili
- indice testuale BM25/FTS

La retrieval sarà **ibrida**:
1. filtro metadati
2. ricerca semantica
3. ricerca testuale
4. reranking
5. risposta con citazioni

### G. Query API / UI
Espone:
- interrogazione in linguaggio naturale
- risultati citati
- spiegazione della copertura (“nel KB non ho evidenza sufficiente”)
- vista documenti / run / errori / metriche qualità

---

## 2. Stack tecnologico scelto

Scelta principale: **Python** end-to-end.

Motivazione: ecosistema migliore per scraping, parsing PDF, OCR, NLP, embedding, API e automazione locale.

### Runtime e API
- **Python 3.12**
- **FastAPI** per API HTTP locali e UI backend: è adatto a API tipizzate e genera documentazione interattiva automaticamente.
- **Typer** o CLI interna per operazioni manuali (`run-once`, `reindex`, `cleanup`, `doctor`)

### Discovery
- **httpx** per richieste HTTP robuste
- **OpenAlex API** per discovery accademico e metadati iniziali
- **Crossref REST API** per consolidare DOI e metadati
- **Unpaywall** come fonte secondaria per URL open-access alternativi quando il DOI è noto

OpenAlex espone opere accademiche e lookup per DOI; Crossref espone retrieval metadati per DOI.

### Parsing documenti
- **Docling** come parser principale: supporta PDF, HTML e altri formati, ha esecuzione locale e supporto OCR per PDF scansionati.
- **PyMuPDF** come fallback veloce per estrazione testo e blocchi da PDF digitali.
- **GROBID** come parser specializzato per articoli scientifici PDF e metadata extraction in TEI/XML; utile soprattutto sui paper.
- **Tesseract OCR** come fallback OCR esplicito; supporta un ampio numero di lingue e traineddata separati.

### Storage operativo
- **SQLite** come database primario locale
  - semplice da fare backup
  - zero servizio esterno
  - ottimo per job log, metadati, stati, dedup map
- **SQLite FTS5** per full-text search locale complementare al vettoriale. FTS5 è il modulo SQLite per la ricerca full-text.

### Retrieval semantica
- **Qdrant** locale per indice vettoriale
  - persistente su disco
  - adatto a filtri metadati
  - facile da gestire su singola macchina
  - può lavorare anche con storage locale/on-disk persistente.

### Embedding e reranking
- **Sentence Transformers** per embedding multilingue locali
- modello embedding multilingue open-weight, così puoi indicizzare IT/EN/FR/ES nello stesso spazio vettoriale; SBERT documenta modelli multilingue pensati per produrre embedding comparabili tra lingue.
- reranker cross-encoder opzionale in seconda fase

### Scheduling e robustezza
- **systemd service + systemd timer** su Linux per esecuzione ricorrente, supervisione e restart policy. I timer systemd attivano servizi su base temporale.

### Configurazione e segreti
- `.env` + file YAML
- credenziali in environment variables
- nessuna chiave hardcoded

### Logging e osservabilità
- logging strutturato JSON
- report HTML/Markdown per ogni run
- eventualmente metrics semplici su SQLite, senza introdurre Prometheus all’inizio

---

## 3. Flusso dati end-to-end

### Step 1: trigger
Il timer lancia un servizio `kb-harvest.service`.

### Step 2: lock del run
Il sistema crea:
- `run_id`
- lock esclusivo
- record iniziale in tabella `runs`

Se esiste un run attivo scaduto, applica recovery policy.

### Step 3: discovery
Per ogni area tematica e lingua:
- genera query predefinite e query espanse con sinonimi  
  esempio: `spirulina`, `arthrospira`, `limnospira`, `photobioreactor`, `airlift`, `food safety`, `ISO 22716`, ecc.
- interroga fonti prioritarie
- produce una coda di candidati

### Step 4: scoring iniziale dei candidati
Ogni candidato riceve:
- `source_authority_score`
- `topic_match_score`
- `language_score`
- `document_type_score`
- `freshness_score`
- `open_access_likelihood`

### Step 5: acquisizione
Per ogni candidato:
- verifica dedup URL/DOI
- scarica asset
- calcola hash binario
- salva raw file e response metadata

### Step 6: parsing
Pipeline a cascata:
1. HTML → estrazione readability-like + cleaning
2. PDF digitale → Docling / PyMuPDF
3. PDF scientifico complesso → opzionalmente GROBID
4. PDF immagine → OCR

### Step 7: arricchimento bibliografico
Normalizza:
- DOI
- titolo
- autori
- anno
- journal / publisher / institution
- lingua
- URL canonical
- open-access URL alternativo
- classificazione tematica

### Step 8: quality gate
Calcola segnali:
- densità termini Spirulina
- presenza sezioni tipiche di paper / report
- rapporto testo/rumore
- lingua coerente
- duplicazione contenuto
- autorevolezza dominio

Esito:
- `accepted`
- `accepted_with_warning`
- `rejected_noise`
- `rejected_offtopic`
- `duplicate`

### Step 9: chunking
Il documento accettato viene spezzato in chunk:
- per sezione, non solo per dimensione fissa
- con overlap moderato
- metadati ereditati dal documento

### Step 10: embedding e indexing
Per ciascun chunk:
- embedding multilingue
- scrittura su Qdrant
- testo e metadati su SQLite/FTS

### Step 11: report di run
Genera:
- report leggibile
- metriche
- warning
- error summary
- top new sources

### Step 12: query utente
Quando chiedi:

> “Quale configurazione di aereazione è raccomandata per un airlift da 50L?”

la pipeline fa:
1. parse domanda
2. eventuale query expansion
3. retrieval ibrida su chunk
4. reranking
5. estrazione evidenze
6. risposta strutturata con citazioni documento + chunk
7. se evidenza insufficiente, lo dichiara esplicitamente

---

## 4. Schema dati

Ti conviene un modello relazionale semplice, con indice vettoriale separato.

### Tabella `documents`
Un record per documento logico.

Campi principali:
- `id`
- `canonical_url`
- `source_url`
- `pdf_url`
- `open_access_url`
- `title`
- `abstract`
- `authors_json`
- `year`
- `doi`
- `journal`
- `publisher`
- `institution`
- `language`
- `doc_type` (`paper`, `guideline`, `thesis`, `manual`, `regulation`, `webpage`)
- `source_domain`
- `topic_tags_json`
- `sha256_binary`
- `sha256_text`
- `status`
- `quality_score`
- `relevance_score`
- `created_at`
- `updated_at`
- `first_seen_run_id`
- `last_seen_run_id`

### Tabella `document_versions`
Per gestire aggiornamenti o cambiamenti di contenuto sulla stessa URL.
- `document_id`
- `version_no`
- `retrieved_at`
- `etag`
- `last_modified`
- `sha256_binary`
- `sha256_text`
- `raw_path`
- `parsed_path`

### Tabella `chunks`
- `id`
- `document_id`
- `version_no`
- `chunk_index`
- `section_title`
- `text`
- `token_count`
- `char_count`
- `page_from`
- `page_to`
- `language`
- `chunk_sha256`
- `citation_label`
- `metadata_json`

### Collezione vettoriale `qdrant/chunks`
Payload:
- `chunk_id`
- `document_id`
- `doi`
- `year`
- `language`
- `doc_type`
- `topic_tags`
- `source_domain`
- `quality_score`

### Tabella `candidates`
Per audit completo del discovery.
- `id`
- `run_id`
- `query`
- `candidate_url`
- `title_hint`
- `source_domain`
- `language_hint`
- `topic_hint`
- `discovery_channel`
- `status`
- `drop_reason`

### Tabella `runs`
- `run_id`
- `started_at`
- `finished_at`
- `status`
- `docs_found`
- `docs_downloaded`
- `docs_parsed`
- `docs_indexed`
- `docs_accepted`
- `docs_rejected`
- `warnings_count`
- `errors_count`
- `report_path`

### Tabella `run_events`
Log applicativo strutturato:
- `timestamp`
- `run_id`
- `phase`
- `severity`
- `entity_type`
- `entity_id`
- `message`
- `details_json`

### Tabella `query_logs`
Per migliorare retrieval in seguito.
- `id`
- `asked_at`
- `question`
- `filters_json`
- `retrieved_chunk_ids_json`
- `answer_status`
- `latency_ms`

---

## 5. Strategia di scheduling e robustezza

### Scheduling
Su Linux farei:
- `systemd service` per il job
- `systemd timer` per la ricorrenza

Esempio concettuale:
- run leggero 4 volte al giorno
- run più pesante notturno 1 volta al giorno
- cleanup settimanale

Perché così:
- niente scheduler dentro l’app
- restart e log centralizzati
- semplice supervisione OS-level

### Locking
Lock file + lock DB:
- impedisce run concorrenti
- consente recovery se processo muore

### Retry
Policy per classe di errore:
- timeout rete: retry con exponential backoff
- 403/429: cooldown dominio
- parse error: fallback parser
- OCR error: marca warning, non blocca run

### Timeout
Timeout separati per:
- discovery request
- download
- parsing
- embedding

### Idempotenza
Ogni fase deve poter essere rieseguita:
- candidato già visto → skip
- documento già uguale → skip
- chunk già indicizzato con stesso hash → skip

### Cleanup
Conservare:
- documenti raw finali
- testi parsati
- DB SQLite
- indice vettoriale
- report per run

Eliminare:
- file temporanei OCR
- HTML intermedi transienti
- artefatti parser corrotti
- download incompleti oltre TTL

---

## 6. Strategia di deduplication

La farei su 4 livelli.

### Livello 1: URL normalization
Normalizzi:
- scheme
- trailing slash
- query params irrilevanti
- fragment
- redirect final URL
- canonical URL se presente

Serve a evitare duplicati banali.

### Livello 2: DOI / identifier
Se il DOI coincide, il documento è quasi certamente lo stesso record logico, anche se hai più URL.

Priorità identificatori:
1. DOI
2. PMID / PMCID / OpenAlex ID se disponibile
3. titolo+autori+anno fuzzy

### Livello 3: hash binario
Se due PDF hanno lo stesso binario → duplicato perfetto.

### Livello 4: hash testuale / similarity
Per versioni diverse dello stesso contenuto:
- normalizzazione testo
- hash del testo pulito
- MinHash / SimHash opzionale
- soglia similarity per near-duplicate

Policy:
- stesso DOI + testo quasi uguale → merge
- URL diverso + testo uguale → alias dello stesso documento
- testo molto simile ma metadata diversi → flag review automatica, non merge cieco

---

## 7. Interfaccia utente

Per la fase iniziale farei **web app locale molto semplice**, non desktop nativo.

### UI minima utile
Tre schermate:

### A. Ask
Una search box in linguaggio naturale.
Output:
- risposta sintetica
- punti chiave
- livello di confidenza
- citazioni cliccabili
- evidenze testuali usate
- messaggio esplicito se il KB non copre la domanda

### B. Library
Elenco documenti con filtri:
- tema
- lingua
- tipo fonte
- anno
- dominio
- accepted / rejected / duplicate

### C. Runs & Health
Dashboard operativa:
- ultimi run
- documenti trovati/scartati
- fallimenti per dominio
- copertura per topic
- warning qualità
- spazio occupato

### API
Esporrei anche endpoint locali:
- `POST /query`
- `POST /runs/trigger`
- `GET /runs`
- `GET /documents`
- `GET /health`
- `POST /reindex/document/{id}`

---

## 8. Punti critici e rischi

### 1. Qualità dei PDF
Molti PDF sono pessimi:
- due colonne
- scansioni
- formule
- tabelle
- testo fuori ordine

Mitigazione:
- parser multipli
- fallback OCR
- scoring di qualità estrazione

### 2. Rumore nella discovery web
Query troppo larghe catturano contenuti commerciali, blog scarsi, pagine SEO.

Mitigazione:
- whitelisting/parziale priorità per domini autorevoli
- classificatore di pertinenza
- soglie minime di qualità

### 3. Multilingua vera
Italiano, inglese, francese, spagnolo richiedono:
- discovery per lingua
- OCR language packs
- embedding robusti cross-lingua

### 4. Normativa
La parte normativa cambia nel tempo e spesso non è comodamente “paper-like”.

Mitigazione:
- pipeline separata per documenti normativi
- tag `jurisdiction`
- evidenza su data/versione del documento

### 5. Hallucination nella fase di answering
Il rischio più serio lato UX.

Mitigazione obbligatoria:
- retrieval-first
- risposta solo dalle evidenze recuperate
- soglia minima di supporto
- citazioni chunk-level
- fallback “non ho evidenza nel KB”

### 6. Crescita disordinata del KB
Senza topic taxonomy chiara, dopo pochi mesi il corpus diventa difficile da governare.

Mitigazione:
- tassonomia fissa iniziale
- topic primari + secondari
- versionamento della tassonomia

### 7. Manutenzione parser esterni
Docling, GROBID, OCR e modelli possono cambiare comportamento.

Mitigazione:
- pipeline a plugin
- contratti interni stabili
- benchmark piccolo di regressione su 20–30 documenti campione

---

## 9. Cosa lascerei fuori dallo scope iniziale

Per partire bene, **non implementerei subito**:

### A. OCR “pesante” su tutto
Lo farei solo fallback.
Motivo: costoso, lento, spesso inutile sui PDF digitali.

### B. Reranking complesso con LLM
All’inizio basta:
- embedding multilingue
- retrieval ibrida
- citazioni
- answer synthesis controllata

### C. Ingestione di immagini, grafici e tabelle strutturate avanzate
Valore alto, ma aumenta molto la complessità.

### D. Auto-classificazione normativa molto fine
All’inizio basta distinguere:
- food safety
- cosmetics
- GMP/HACCP/ISO
- contaminanti / microbiologia
- labeling / packaging

### E. Feedback loop umano complesso
Per MVP bastano query log e qualche flag manuale documento.

### F. Deployment cross-platform
Io lo progetterei per **Linux locale**. Portabilità a Windows/macOS viene dopo.

---

## Decisione architetturale consigliata

Se dovessi scegliere una baseline concreta oggi, partirei con questa:

- **Python + FastAPI**
- **SQLite + FTS5**
- **Qdrant locale**
- **Docling come parser principale**
- **PyMuPDF fallback**
- **GROBID solo per i paper scientifici**
- **Tesseract OCR solo fallback**
- **Sentence Transformers multilingue**
- **systemd timer/service**
- **discovery guidato da OpenAlex + Crossref + query web su domini autorevoli**

È la combinazione che massimizza:
- semplicità operativa
- esecuzione locale
- auditabilità
- qualità scientifica del corpus
- possibilità di evolvere verso una KB davvero utile per decisioni progettuali

## In che ordine implementerei

1. modello dati + logging + run lifecycle  
2. discovery + acquisition  
3. parsing + enrichment  
4. dedup + quality gate  
5. chunking + embeddings + indice ibrido  
6. query API + UI minima  
7. reportistica + cleanup + benchmark di regressione

Alla tua conferma, nel passo successivo posso trasformare questa architettura in un **piano di sviluppo esecutivo** molto concreto: struttura repository, moduli, contratti tra componenti, milestone, e poi partire con l’implementazione vera.

