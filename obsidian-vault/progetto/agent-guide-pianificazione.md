# Guida per agenti — sistema di pianificazione AlgaVitae

Questo file documenta come è strutturato il sistema di pianificazione del progetto AlgaVitae e come un agente deve interagire con esso. Leggilo prima di qualsiasi intervento sui file di pianificazione.

---

## Cos'è AlgaVitae

Startup di coltivazione e vendita di spirulina, con sede a Montegranaro (FM), Marche. L'obiettivo è arrivare alla prima vendita entro agosto 2026, partendo da zero (nessun impianto, nessun prodotto, nessun cliente).

Il progetto è condotto da una singola persona (il fondatore). Ogni decisione è sua. Il ruolo dell'agente è supportare la pianificazione, aggiornare i file, segnalare incoerenze, proporre opzioni — mai decidere in autonomia su scelte di business.

---

## Struttura del sistema di file

### Gerarchia pianificazione

```
gantt-piano-operativo-algavitae.md          ← Master plan (4 fasi, milestone GO/NO-GO)
  └→ gantt-piano-operativo-algavitae-[mese].md   ← Piano mensile (task per settimana)
       └→ operativo-[mese].md                    ← Esecutivo (task candidati + check-in giornaliero)
```

### Artefatti di progetto (knowledge base decisionale)

```
artefatti-progetto.md                  ← Regole di manutenzione degli artefatti
  ├→ Registro Decisionale.md           ← Tutte le decisioni prese (D-YYYY-MM-DD-XX)
  ├→ Registro Evidenze.md              ← Fonti, call, interviste, dati (E-YYYY-MM-DD-XX)
  ├→ memo-normativo.md                 ← Vincoli normativi emersi
  ├→ customer-discovery.md             ← Interviste e segnali clienti (C-YYYY-MM-DD-XX)
  ├→ financial-model.md                ← Budget, CAPEX, OPEX, scenari, break-even
  └→ competitors/competitor-map.md     ← Master competitor (F-YYYY-MM-DD-XX per note finanziarie)
```

### File di supporto

```
TODO.md                                ← Task urgenti non legati al Gantt
inoculo.md                             ← Stato acquisizione ceppo spirulina
dati-climatici-fermo-marche.md         ← Baseline climatica consolidata (non modificare)
competitors/inbox/                     ← Run automatici (NON modificare manualmente)
competitors/archive/                   ← Storico run automatici
```

---

## Ruolo di ogni file

### `gantt-piano-operativo-algavitae.md` — master plan
- Contiene le 4 fasi del progetto e le milestone GO/NO-GO
- Modificalo solo se cambia la strategia complessiva o slittano milestone maggiori
- **Frequenza di aggiornamento:** raramente (una volta al mese o meno)

### `gantt-piano-operativo-algavitae-[mese].md` — piano mensile
- Contiene il Gantt Mermaid con task settimanali e la checklist operativa del mese
- Modificalo per aggiornare lo stato dei task (`:done`, `:active`) e spuntare le checkbox
- **Frequenza di aggiornamento:** fine settimana o quando scatta una milestone
- **Crea un nuovo file** a inizio di ogni mese (es. `gantt-piano-operativo-algavitae-maggio.md`)

### `operativo-[mese].md` — esecutivo giornaliero
- Contiene i task candidati di ogni settimana, il piano giornaliero suggerito, i check-in e le review
- È il file che il fondatore usa ogni giorno
- **Frequenza di aggiornamento:** ogni giorno (check-in) e ogni settimana (review)
- Deve sempre avere sezioni per **tutte le settimane del mese** già popolate
- **Crea un nuovo file** a inizio di ogni mese

### `Registro Decisionale.md`
- Ogni decisione ha ID `D-YYYY-MM-DD-XX`, stato (`provvisoria` / `confermata` / `superata`), motivazione, evidenze, impatto, open point, next step
- Non modificare decisioni esistenti: aggiungi nuove voci o cambia lo stato
- Aggiornalo ogni volta che il fondatore prende o conferma una decisione

### `Registro Evidenze.md`
- Ogni evidenza ha ID `E-YYYY-MM-DD-XX`, tipo, fonte, sintesi, affidabilità, implicazione pratica, decisioni collegate
- Aggiungilo ogni volta che arriva un input esterno rilevante (call, preventivo, intervista, dato)

### `memo-normativo.md`
- Alimentato da call con consulente regolatorio e ricerche normative
- Non inventare requisiti: aggiungi solo ciò che emerge da fonti certe

### `customer-discovery.md`
- Ogni intervista ha ID `C-YYYY-MM-DD-XX`, segmento, insight, segnali positivi, obiezioni, implicazione pratica, next step
- Non modificare voci esistenti: aggiungi sempre nuove schede

### `financial-model.md`
- Contiene budget certo (30.000 €), budget obiettivo (38.000 €), budget massimo (55.000 €)
- Aggiorna con dati reali man mano che arrivano preventivi e stime
- Non cambiare le cifre senza istruzione esplicita del fondatore

### `competitors/competitor-map.md`
- File master consolidato dal fondatore
- Gli aggiornamenti automatici arrivano in `competitors/inbox/` — non promuoverli automaticamente
- Aggiungi voci solo su istruzione esplicita del fondatore, dopo review umana dell'inbox

---

## Naming convention

| Tipo | Pattern | Esempio |
|------|---------|---------|
| Decisione | `D-YYYY-MM-DD-XX` | `D-2026-03-30-01` |
| Evidenza | `E-YYYY-MM-DD-XX` | `E-2026-04-08-01` |
| Intervista cliente | `C-YYYY-MM-DD-XX` | `C-2026-04-09-01` |
| Nota finanziaria | `F-YYYY-MM-DD-XX` | `F-2026-04-15-01` |
| Competitor | `COMP-YYYY-MM-DD-XX` | `COMP-2026-03-31-01` |

---

## Stati ammessi

### Task operativi (operativo-[mese].md)
- `da fare` — non ancora iniziato
- `in corso` — lavoro avviato
- `fatto` — output concreto prodotto
- `bloccato` — non proseguibile senza un'azione esterna
- `rimandato` — spostato consapevolmente

### Decisioni (Registro Decisionale)
- `provvisoria` — assunta ma non ancora validata da evidenze sufficienti
- `confermata` — validata da evidenze
- `superata` — invalidata o sostituita da decisione successiva

### Gantt Mermaid (piano mensile)
- `:done` — completato
- `:active` — in corso
- `:milestone` — gate critico (diamante nel diagramma)
- nessun tag — schedulato/futuro

---

## Milestone del progetto

| ID | Data | Decisione |
|----|------|-----------|
| M1 | 26 apr 2026 | Decisione su direzione generale (fattibilità business case) |
| M2 | 24 mag 2026 | Decisione su architettura tecnica (impianto) |
| M3 / GO-NO-GO | 21 giu 2026 | Decisione di avvio operativo |
| Prima vendita | 31 ago 2026 | Target operativo |

**Decisioni obbligatorie entro M1 (o M2 al massimo):**
- food vs cosmetic
- fresco vs secco
- architettura impianto

---

## Stato corrente (da aggiornare a ogni ripresa)

> Aggiorna questa sezione ogni volta che riprendi il lavoro sul sistema.

**Ultima revisione:** 2026-04-08  
**Settimana in corso:** Settimana 2 (6–12 aprile)  
**Prossima milestone:** M1 — 26 aprile 2026

---

### Artefatti e versioni

| Artefatto | Versione | Stato |
|-----------|----------|-------|
| Registro Decisionale | — | 2 decisioni provvisorie (D-2026-03-30-01 budget, D-2026-03-30-02 bandi) |
| Registro Evidenze | — | Template + 1 voce (E-2026-03-30-01) |
| Memo normativo | v0 | Solo template — attende call consulente regolatorio |
| Customer discovery | v0 | Solo template — interviste non ancora avviate |
| Financial model | v0 | Budget definito (30k/38k/55k), CAPEX/OPEX/break-even mancanti |
| Competitor map | v0.1 | 14+ competitor mappati, pricing parziale |
| Dati climatici | consolidato | Non aggiornare — baseline completa |

---

### Settimana 1 — consuntivo (30 mar – 2 apr)

| Task | Stato |
|------|-------|
| Definire budget massimo investibile | fatto |
| Creare Registro Decisionale | fatto |
| Creare Registro Evidenze | fatto |
| Creare file master artefatti progetto | fatto |
| Raccogliere dati climatici Marche/Fermo | fatto |
| Estrarre digest KB dai focus prioritari | fatto |
| Mappare almeno 10 competitor | fatto |
| Preparare lista almeno 10 contatti cliente | **da fare** |
| Contattare consulente regolatorio | **da fare** |
| Contattare almeno 2 produttori italiani | **da fare** |

---

### Settimana 2 — stato al 8 aprile

| Task | Stato |
|------|-------|
| Chiamata consulente regolatorio | da fare |
| Scrivere memo normativo v0.1 | da fare |
| Prime 3 interviste cliente | da fare |
| Chiamata con almeno 1 produttore italiano | da fare |
| Aggiornare competitor pricing | da fare |
| Review focus KB #1 | da fare |

---

### Blocchi aperti critici

| Blocco | Dettaglio | Impatto |
|--------|-----------|---------|
| Inoculo ACUF 677 | Email inviata a info@acuf.net (marzo 2026), nessuna risposta | Blocca avvio coltura |
| Inoculo BEA 0873B | Email inviata (marzo 2026), nessuna risposta | Fallback inoculo |
| Consulente regolatorio | Nessun appuntamento confermato | Blocca memo normativo v0.1 e parte di M1 |
| Preventivi impianto | Non ancora richiesti | Blocca financial model v0.1 e M1 |
| Diametro collo damigiana 50L | Da misurare fisicamente | Blocca acquisto tappo silicone con foro |

---

### Hardware ordinato (non aspettare conferme)

- DFRobot SEN0169-V2 (pH) — ordinato
- DFRobot DFR0300 IP68 (EC K=1) — ordinato
- DS18B20 impermeabile — ordinato
- D4184 MOSFET module (dimmer LED ESP32) — ordinato

---

## Regole operative per l'agente

### Cosa puoi fare senza chiedere
- Aggiungere sezioni mancanti a `operativo-[mese].md` (settimane non ancora create)
- Aggiornare lo stato di un task da `da fare` a `in corso` o `fatto` se il fondatore lo conferma nella conversazione
- Aggiungere nuove schede a Registro Evidenze o customer-discovery.md su istruzione esplicita
- Spuntare checkbox nel gantt mensile su istruzione esplicita
- Aggiornare la sezione "Stato corrente" in questo file

### Cosa devi sempre chiedere prima
- Modificare o segnare come `superata` una decisione esistente nel Registro Decisionale
- Cambiare cifre nel financial-model.md
- Promuovere voci da `competitors/inbox/` a `competitors/competitor-map.md`
- Creare nuove decisioni (puoi proporre la bozza, ma il fondatore deve approvarla)
- Slittare una milestone nel master plan

### Non fare mai
- Inventare dati (preventivi, prezzi, stime normative) non forniti esplicitamente
- Modificare `dati-climatici-fermo-marche.md` (consolidato, non aggiornare)
- Scrivere in `competitors/inbox/` o `competitors/archive/` (gestiti da script automatici)
- Segnare M1/M2/GO-NO-GO come completate senza istruzione esplicita

---

## Ritmo operativo consigliato

| Momento | Azione | File |
|---------|--------|------|
| Ogni mattina | Check-in mattutino | `operativo-[mese].md` |
| Ogni sera | Check-in serale | `operativo-[mese].md` |
| Fine settimana | Review settimanale + aggiorna Gantt | `operativo-[mese].md`, `gantt-[mese].md` |
| Ogni volta che arriva un input esterno | Aggiungi voce a Registro Evidenze | `Registro Evidenze.md` |
| Ogni volta che si prende una decisione | Aggiungi voce a Registro Decisionale | `Registro Decisionale.md` |
| Fine mese | Consolida artefatti v0.1, crea piano mese successivo | tutti gli artefatti |

---

## Come riprendere il lavoro dopo una pausa

1. Leggi **questa guida** (sei già qui)
2. Leggi **"Stato corrente"** sopra — 2 minuti
3. Apri `gantt-piano-operativo-algavitae-[mese corrente].md` — identifica la settimana in corso
4. Apri `operativo-[mese corrente].md` — identifica i task aperti della settimana
5. Controlla `TODO.md` — ci sono task urgenti fuori Gantt?
6. Controlla `Registro Decisionale.md` sezione "Open point" — cosa è rimasto in sospeso?
7. Chiedi al fondatore: *"Cosa è cambiato dall'ultima sessione?"* e aggiorna gli stati

Non leggere tutti i file prima di iniziare: parti dallo stato corrente e approfondisci solo dove serve.
