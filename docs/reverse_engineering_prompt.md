# Prompt — Sviluppo da zero: sistema di knowledge base automatico per Spirulina

---

## Contesto

Sto avviando una micro-produzione di Spirulina (*Arthrospira platensis* / *Limnospira maxima*) a uso alimentare e cosmetico, con un fotobioreattore da 50 litri. Ho bisogno di un sistema software che costruisca e mantenga automaticamente una knowledge base tecnico-scientifica su questo argomento, e che mi permetta di interrogarla in linguaggio naturale per ottenere risposte progettuali supportate da fonti.

---

## Cosa deve fare il sistema

### 1. Raccolta automatica delle fonti

Il sistema deve cercare autonomamente, su base ricorrente (più volte al giorno), documenti tecnici e scientifici relativi alla coltivazione di Spirulina. Le fonti di interesse includono:

- Paper accademici open-access
- Report tecnici, tesi, linee guida istituzionali
- Documentazione normativa (sicurezza alimentare, cosmetica, certificazioni)
- Manuali operativi e procedure

La ricerca deve coprire almeno le seguenti aree tematiche:
- Fotobioreattori (geometria, aereazione, mixing, scale-up)
- Controllo di processo (pH, CO₂, temperatura, bicarbonato)
- Illuminazione (spettro LED, PPFD, cicli luce/buio)
- Raccolta e filtrazione della biomassa
- Analisi e sicurezza della biomassa (EFSA, FDA, metalli pesanti, microbiologico)
- Certificazioni food/cosmetic (GMP, HACCP, ISO 22000, ISO 22716)
- Ceppi di coltivazione (collezioni internazionali, caratterizzazione)
- Economia circolare (acque reflue, digestato, nutrient recovery)
- Packaging, shelf life, commercializzazione
- Coltivazione domestica e piccola scala

La ricerca deve essere **multilingue** (italiano, inglese, francese, spagnolo almeno) e favorire documenti da fonti istituzionali autorevoli (università, enti governativi, organismi internazionali come FAO, EFSA, WHO).

### 2. Acquisizione e parsificazione documenti

Il sistema deve scaricare i documenti trovati (HTML e PDF) ed estrarne il testo in forma leggibile, rimuovendo boilerplate, navigazione, banner pubblicitari e altri elementi non-contenuto.

Deve gestire:
- PDF accademici (inclusi PDF con solo immagini — OCR)
- Pagine HTML di publisher scientifici
- Documenti istituzionali

Deve arricchire ogni documento con metadati bibliografici: DOI, autori, anno di pubblicazione, journal, URL PDF open-access alternativo quando disponibile.

### 3. Indicizzazione per ricerca semantica

Il testo estratto deve essere reso ricercabile tramite **similarità semantica** (non solo keyword matching). Il sistema deve poter rispondere a domande in linguaggio naturale trovando i passaggi rilevanti anche se usano terminologia diversa dalla query.

### 4. Controllo qualità automatico

Ad ogni ciclo di raccolta, il sistema deve verificare che:
- Siano stati trovati abbastanza documenti nuovi
- Le fonti siano diversificate (no dominio singolo dominante)
- I documenti siano effettivamente pertinenti a Spirulina
- Il contenuto non sia degradato o pieno di rumore

Il risultato del controllo qualità deve essere loggato e non deve interrompere il ciclo in caso di fallimento parziale.

### 5. Report automatico

Ad ogni ciclo, il sistema deve generare un report leggibile che riassuma:
- Quanti documenti sono stati trovati, scaricati, indicizzati
- Da quali fonti e aree tematiche
- Eventuali fallimenti (download bloccati, siti irraggiungibili, ecc.)
- Segnali di qualità del contenuto

### 6. Interfaccia di interrogazione

Il sistema deve offrire un modo per fare domande in linguaggio naturale al knowledge base e ricevere risposte strutturate con citazioni. Le risposte devono:
- Essere basate sui documenti indicizzati, non su conoscenza generica del modello
- Citare le fonti usate
- Indicare esplicitamente quando l'informazione non è disponibile nel KB (nessuna allucinazione)
- Essere utili per prendere decisioni progettuali (es. "Quale configurazione di aereazione è raccomandata per un airlift da 50L?")

### 7. Gestione della deduplication

Il sistema deve evitare di scaricare e indicizzare più volte lo stesso documento. Deve riconoscere duplicati sia per URL che per contenuto.

### 8. Manutenzione automatica

Il sistema deve gestire autonomamente la pulizia degli artefatti intermedi per non occupare spazio disco illimitato, mantenendo però i dati permanenti (testi, indice vettoriale).

---

## Vincoli operativi

- Il sistema deve girare su una singola macchina locale (non cloud), in modo non supervisionato.
- Deve essere robusto: se un servizio si blocca o un download fallisce, il ciclo deve continuare e completarsi.
- Deve essere idempotente: rieseguire lo stesso ciclo non deve produrre duplicati.
- Deve essere diagnosticabile: ogni run deve lasciare tracce sufficienti per capire cosa è successo senza dover rieseguire.
- Le credenziali API (chiavi di ricerca, chiavi LLM) devono essere configurabili senza toccare il codice.

---

## Non è richiesto

- Alta disponibilità o scalabilità orizzontale
- Multi-utente o autenticazione
- Interfaccia mobile
- Deployment cloud

---

## Prima di iniziare lo sviluppo

**Non scrivere ancora nessun codice.**

Prima di procedere con qualsiasi implementazione, descrivimi in dettaglio:

1. **Architettura complessiva**: quali componenti prevedi, come si parlano, dove vivono i dati.
2. **Stack tecnologico scelto**: linguaggi, librerie, database, servizi — con motivazione per ogni scelta.
3. **Flusso dati end-to-end**: dal momento in cui il sistema cerca una fonte al momento in cui la risposta arriva all'utente.
4. **Schema dati**: come rappresenti un documento, un chunk, i metadati bibliografici, i risultati di ricerca.
5. **Strategia di scheduling e robustezza**: come gestisci l'esecuzione ricorrente, i retry, i lock, il cleanup.
6. **Strategia di deduplication**: come eviti duplicati a livello di URL, contenuto e DOI.
7. **Interfaccia utente**: come si interroga il sistema, cosa vede l'utente.
8. **Punti critici e rischi**: cosa potrebbe andare storto, cosa richiede attenzione particolare.
9. **Cosa lasci fuori dallo scope iniziale**: cosa non implementeresti subito e perché.

Solo dopo aver ricevuto la mia conferma su questa descrizione, procedi con lo sviluppo.
