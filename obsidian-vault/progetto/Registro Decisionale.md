# Registro Decisionale — Algavitae

Questo file raccoglie le decisioni prese, le ipotesi alla base, i criteri usati e gli eventuali punti aperti.

## Regole d'uso
- Una riga/voce per ogni decisione rilevante.
- Scrivere sempre: data, decisione, motivazione, evidenze usate, impatto e next step.
- Se una decisione è provvisoria, marcarla come `provvisoria`.
- Se una decisione viene cambiata, non cancellare la precedente: aggiungi una nuova voce con riferimento alla vecchia.

---

## Decisioni attive

### D-2026-03-30-01 — Budget massimo investibile
- **Data:** 2026-03-30
- **Stato:** provvisoria
- **Decisione:** Il budget massimo investibile verrà stimato come somma tra budget personale investibile e massimo finanziabile ottenibile tramite bandi/contributi.
- **Formula adottata:**
  - budget certo = 30.000 €
  - budget obiettivo = 30.000 € + bandi probabili
  - budget massimo = 30.000 € + bandi massimi teorici
- **Motivazione:** approccio semplice e pratico per fissare subito un perimetro economico senza costruire un modello finanziario completo in questa fase.
- **Evidenze collegate:** Registro Evidenze + fonti placeholder budget del 2026-03-30
- **Impatto:** definisce il perimetro iniziale per valutare architetture, preventivi e fattibilità economica.
- **Open point:** stimare bandi probabili e bandi massimi teorici realistici.
- **Next step:** raccogliere una prima lista di bandi/agevolazioni applicabili e stimare importi finanziabili.

### D-2026-03-30-02 — Placeholder bandi per budget iniziale
- **Data:** 2026-03-30
- **Stato:** provvisoria
- **Decisione:** Per il budget iniziale si adottano, come placeholder di lavoro, **8.000 €** di bandi probabili e **25.000 €** di bandi massimi teorici.
- **Motivazione:** tra le due stime esterne disponibili, la versione prudente è più coerente con lo stato reale del progetto: fase molto iniziale, nessuna forma giuridica ancora definita, architettura finale non scelta, business plan non consolidato, pipeline clienti assente e forte dipendenza da variabili non ancora risolte (inquadramento agricolo vs startup innovativa, percorso regolatorio, tempistiche di costituzione impresa). La stima più alta (**25k / 100k**) è utile come scenario esplorativo ampio, ma oggi appare troppo aggressiva per essere usata come placeholder decisionale di base.
- **Evidenze collegate:**
  - Fonte placeholder Anthropic/Sonnet: `progetto/fonti-placeholder-budget/2026-03-30-anthropic-agevolazioni.md`
  - Fonte placeholder GPT: `progetto/fonti-placeholder-budget/2026-03-30-gpt-placeholder-grants.md`
- **Impatto:** il perimetro di lavoro attuale diventa:
  - budget certo = **30.000 €**
  - budget obiettivo = **38.000 €**
  - budget massimo = **55.000 €**
- **Open point:**
  - verificare se la spirulina potrà essere inquadrata in modo da accedere a canali agricoli / CSR Marche
  - chiarire se la forma giuridica finale potrà essere compatibile con strumenti più forti (es. SRL innovativa)
  - capire se i bandi regionali/nazionali disponibili nel 2026–2027 supportano davvero il progetto con questo profilo
- **Next step:** mantenere **8k / 25k** come placeholder decisionale prudente fino alla grant scan strutturata di maggio/giugno; usare invece **25k / 100k** solo come scenario alto da stress-testare, non come base decisionale.

---

## Template nuova decisione

### D-YYYY-MM-DD-XX — Titolo decisione
- **Data:** YYYY-MM-DD
- **Stato:** provvisoria / confermata / superata
- **Decisione:**
- **Motivazione:**
- **Evidenze collegate:**
- **Impatto:**
- **Open point:**
- **Next step:**

---

## Decisioni da prendere
- [ ] Quantificare bandi probabili con grant scan reale
- [ ] Quantificare bandi massimi teorici con forma giuridica e contesto produttivo definiti
- [ ] Decisione food vs cosmetic
- [ ] Decisione fresco vs secco
- [ ] Decisione architettura impianto
