# Istruzioni per interrogare la Knowledge Base spiru-ops

## Cos'è questa KB

È una knowledge base tecnico-scientifica su **Spirulina/Arthrospira** costruita con una pipeline automatica giornaliera. Contiene ~67.000 chunk vettorizzati estratti da paper scientifici, pagine web, tesi, linee guida normative e schede tecniche. La KB è accessibile tramite due modalità:

- **SpiruCopilot** (UI Streamlit a `http://localhost:8501`): restituisce una risposta LLM strutturata + citazioni
- **Query CLI** (risposta raw senza LLM): `python pipelines/query.py "query" --topk 10`

---

## Contesto del progetto (sempre applicare)

- **Location**: Fermo, Marche, Italia centrale — clima mediterraneo
- **Scala**: micro-produzione artigianale, ~50 L fotobioreattore (PBR), obiettivo 10–30 g/giorno biomassa secca
- **Prodotti target**: pasta fresca (cosmesi), polvere essiccata (integratore alimentare), estratto di ficocianina
- **Budget**: low-capex, DIY-friendly, materiali preferiti PMMA/PVC/PE alimentare
- **Acqua**: acqua di rete o pozzo → pretratmento UV + RO
- **Quadro normativo EU**: Novel Food Reg. 2015/2283, Dir. 2002/46 integratori, ISO 22000/HACCP, ISO 22716 (cosmetica)

---

## Focus area disponibili come filtro

Usa **sempre** il nome esatto (underscore) quando selezioni il filtro nel copilot:

| Priorità | Nome filtro | Scope |
|---|---|---|
| P0 | `production_system_selection` | Confronto raceway vs PBR, scelta architettura impianto pilota |
| P0 | `seasonal_productivity_italy` | Produttività mensile outdoor, stagionalità Italia |
| P0 | `capex_opex_economics` | Costi CAPEX/OPEX, break-even, modello finanziario startup |
| P0 | `customer_discovery_italy` | Buyer B2B: nutraceutica, cosmetica, HoReCa, distributori Italia |
| P0 | `competitor_pricing_italy_eu` | Produttori italiani/EU, prezzi, profili aziendali |
| P0 | `regulatory_pathway_italy` | SIAN, D.lgs 169/2004, CPNP, Novel Food, HACCP |
| P0 | `food_vs_cosmetic_strategy` | Confronto percorso food vs cosmetic: tempi, costi, margini |
| P1 | `harvesting_and_drying` | Filtrazione, microstrainer, essiccazione spray/freeze |
| P1 | `contamination_management` | Rotiferi, protozoi, batteri: identificazione e protocolli |
| P1 | `process_control_and_cleaning` | Setpoint pH/CO2/temp, CIP, sanificazione |
| P1 | `quality_qc_shelf_life` | Metalli pesanti, microbiologia, analisi in-house, shelf life |
| P1 | `temperature_and_cold_management` | Gestione basse temperature, inverno, costi riscaldamento |
| P1 | `water_site_infrastructure` | Qualità acqua, vincoli sito, scarichi, energia |
| P1 | `strains_inoculum` | Ceppi Arthrospira, collezioni, protocollo scale-up inoculo |
| P2 | `illumination_led_indoor` | LED indoor, PPFD, spettro, costi energetici |
| P2 | `packaging_labeling` | Etichettatura EU, health claims, formati |
| P2 | `fresh_spirulina_market` | Vendita pasta fresca: ristoranti, GAS, HoReCa, prezzo |
| P2 | `sales_channels_italy` | Canali B2B/B2C, distribuzione, e-commerce Italia |
| P2 | `grants_funding` | PSR Marche, FESR, Horizon, Invitalia, bandi 2024-2025 |

---

## Come formulare query efficaci

### Principi generali

1. **Sii specifico su scala e contesto**: aggiungi sempre "50L", "small scale", "Italia", "startup" dove pertinente. La KB sa già che è un PBR a spirulina — non serve ripeterlo ogni volta.

2. **Chiedi un output strutturato**: il copilot risponde meglio a domande che richiedono tabelle, BOM, confronti, protocolli. Domande vaghe producono più TBD.

3. **Usa il filtro focus**: seleziona il focus area pertinente nella sidebar. Senza filtro il retrieval è più rumoroso. Con filtro ottieni i chunk più rilevanti per quel dominio specifico.

4. **Top-K**: usa 10–15 per domande complesse che richiedono più fonti; 5–8 per domande puntuali.

### Esempi di query efficaci

```
# Buona — specifica, chiede output strutturato
"Confronta 3 geometrie di PBR (flat panel, tubular, airlift colonna) per 50L:
path ottico, miscelazione, costo materiali, cleanability. Tabella comparativa."

# Buona — focus operativo con setpoint concreti
"Quali sono i setpoint operativi (pH, T, CO2, irradianza) per massimizzare
la produttività estiva in raceway outdoor a Fermo, Marche?"

# Buona — domanda decisionale con trade-off
"Quali rischi di contaminazione distinguono raceway outdoor da PBR chiuso
in estate a 35°C+? Come si gestisce ciascuno? Protocol di risposta rapida."

# Buona — mercato/clienti
"Quali buyer italiani di ingrediente spirulina B2B sono documentati nella KB?
Distinzione nutraceutica vs cosmetica vs HoReCa."

# Evita — troppo vaga
"Dimmi tutto sulla spirulina"

# Evita — fuori scope della KB
"Qual è il prezzo al kg di spirulina su Amazon oggi?"
```

---

## Come interpretare le risposte del copilot

- **[1], [2], ...**: citazioni da documenti reali nella KB. Ogni claim deve essere supportato da almeno una.
- **TBD**: la KB non contiene evidenza per quel parametro. Il copilot proporrà un esperimento o misura da fare.
- **"Low confidence transfer"**: l'evidenza esiste ma è per microalghe generiche, non Spirulina-specifica. Usare con cautela.
- **Sezione "Sources"**: lista URL alla fine della risposta. Verifica sempre le fonti primarie per decisioni critiche.

Se la risposta ha molti TBD → la KB non copre bene quell'area, oppure il filtro focus era sbagliato.

---

## Workflow suggerito per sessioni di ricerca

1. **Preview evidence** prima di lanciare il copilot: click "Preview evidence" nella UI per vedere quali chunk vengono recuperati. Se sono irrilevanti, cambia focus o riformula la query.

2. **Una domanda per focus area**: non mescolare argomenti diversi in una singola query. Es. non chiedere nello stesso prompt "CAPEX + normativa + contaminazione" — fai 3 query separate con i rispettivi filtri.

3. **Iterazione**: se la risposta ha TBD in punti critici, riformula la query con sinonimi o termini tecnici diversi (es. "microstrainer" → "filtration belt press", "essiccazione" → "spray drying drum drying").

4. **Appendi alla living spec**: la UI ha un checkbox "Append answer to living_spec.md" — usalo per risposte consolidate che vuoi tenere come riferimento cumulativo del progetto.

---

## Limiti noti della KB (aprile 2026)

- **customer_discovery_italy** e **competitor_pricing_italy_eu**: contenuto web (schede aziendali, profili buyer) era assente fino ad aprile 2026 per un bug di configurazione (SearXNG non avviato). Da aprile in poi la coverage migliora.
- **regulatory_pathway_italy**: buona coverage sulla normativa EU generica; più scarsa sui dettagli procedurali SIAN italiani (documenti interni non pubblici online).
- **fresh_spirulina_market**: coverage buona su dati di mercato europei, più debole su prezzi e canali specifici Italia 2024-2025.
- Documenti **paywalled** (Elsevier, Springer senza OA) presenti come abstract/metadata ma non full text → citazioni possibili ma snippet brevi.
