# Dati climatici — Fermo / Montegranaro (Marche)

## Obiettivo
Costruire una baseline climatica sintetica per valutare la compatibilità del sito con:
- serra semi-chiusa
- tubular PBR solare
- indoor PBR

L'obiettivo non è uno studio meteorologico completo, ma una nota decisionale utile per:
- stimare mesi operativi realistici
- capire impatto del clima su CAPEX/OPEX
- supportare la scelta architetturale

---

## Fonti usate

### 1. Open-Meteo Archive API
- Tipo: dati meteo giornalieri storici / modellati
- Punto usato: area Fermo / Montegranaro (~43.128, 13.681, elevazione ~265 m)
- Periodo aggregato: **2023–2025**
- Variabili usate:
  - temperatura media
  - temperatura minima
  - temperatura massima
  - precipitazioni
  - vento max giornaliero
  - shortwave radiation sum
- Nota: è la fonte quantitativa principale usata per la tabella mensile sotto

### 2. meteoblue climate modelled
- Uso: fonte qualitativa di supporto
- Nota utile: i diagrammi climatici meteoblue sono basati su 30 anni di simulazioni orarie di modello e servono come riferimento per pattern climatici tipici, ma con risoluzione ~30 km e senza catturare bene tutti gli effetti locali
- Utilità qui: conferma che temperatura, precipitazioni, sole e vento sono variabili corrette da guardare per una baseline climatica di sito

### 3. PVGIS (JRC)
- Uso: riferimento metodologico per la parte radiazione / irradianza solare
- Utilità qui: conferma che per valutare sistemi solari e serre conviene raccogliere almeno un indicatore di radiazione globale / shortwave

### 4. Risultati di ricerca climate-data / weather-and-climate
- Uso: validazione qualitativa esterna della classificazione climatica locale
- Indicazioni emerse dai risultati: clima mediterraneo caldo-estivo (Csa), temperature annue moderate, estate calda, piogge distribuite ma con picchi stagionali
- Nota: non usati come fonte primaria numerica nel merge finale

---

## Dati sintetici mensili (media 2023–2025)

Unità:
- T media / Tmin / Tmax in °C
- precipitazioni in mm/mese
- vento come media del massimo giornaliero in km/h
- radiazione come kWh/m²/day equivalente da shortwave radiation sum

| Mese | T media | Tmin media | Tmax media | Pioggia | Vento | Radiazione |
|---|---:|---:|---:|---:|---:|---:|
| Gen | 8.1 | 4.9 | 12.4 | 63.7 | 14.3 | 1.8 |
| Feb | 8.8 | 5.1 | 13.5 | 55.8 | 12.6 | 2.7 |
| Mar | 11.5 | 7.5 | 16.3 | 111.4 | 14.7 | 3.9 |
| Apr | 13.6 | 9.1 | 18.5 | 66.6 | 15.2 | 5.4 |
| Mag | 17.6 | 13.3 | 22.2 | 133.1 | 13.8 | 6.1 |
| Giu | 23.5 | 18.5 | 28.4 | 71.2 | 13.4 | 7.1 |
| Lug | 26.5 | 21.2 | 31.6 | 54.9 | 15.3 | 7.1 |
| Ago | 25.5 | 20.7 | 30.9 | 51.8 | 13.5 | 6.2 |
| Set | 21.5 | 17.4 | 26.5 | 98.2 | 14.7 | 4.7 |
| Ott | 17.6 | 13.9 | 22.5 | 72.9 | 14.4 | 3.2 |
| Nov | 11.9 | 8.3 | 16.6 | 99.4 | 15.5 | 2.0 |
| Dic | 8.9 | 5.8 | 13.1 | 58.7 | 14.8 | 1.7 |

---

## Lettura pratica dei dati

### Quadro generale
- Il sito ha un profilo **temperato-mediterraneo** con estate calda e buona radiazione da aprile a settembre.
- I mesi migliori per sistemi **solari / serra** sono chiaramente **aprile–settembre**.
- I mesi più critici per produttività senza supporto termico sono **dicembre–febbraio**.
- I mesi di transizione **marzo, ottobre, novembre** sono gestibili ma richiedono attenzione a resa, variabilità e controllo termico.

### Finestra operativa climatica plausibile
- **Forte favore climatico:** aprile → settembre
- **Finestra intermedia / borderline:** marzo, ottobre
- **Finestra debole / critica:** novembre → febbraio

### Segnali chiave
- La radiazione cresce molto da aprile e raggiunge il picco in **giugno-luglio (~7.1 kWh/m²/day)**.
- In estate le **Tmax medie >30°C** a luglio-agosto indicano rischio reale di surriscaldamento in serra o in sistemi solari molto esposti.
- In inverno le **T medie 8–9°C** sono troppo basse per aspettarsi produttività interessante senza misure correttive, soprattutto per approcci low-tech all'aperto o serra poco controllata.
- Piogge elevate in **marzo, maggio, settembre, novembre** suggeriscono che l'infrastruttura di serra e il sito vadano pensati bene anche in termini di robustezza, drenaggio e gestione umidità.

---

## Implicazioni per il progetto

### 1. Serra semi-chiusa
**Lettura:** opzione abbastanza coerente con il clima locale.

**Vantaggi climatici**
- da aprile a settembre il profilo è favorevole
- la serra può estendere la finestra utile oltre quella di un open raceway puro
- buon compromesso tra sfruttamento della radiazione e contenimento del rischio climatico

**Criticità climatiche**
- estate: rischio surriscaldamento e overexposure
- inverno: temperature troppo basse per contare su buona produttività senza supporto
- mesi piovosi/umidi: attenzione a condensa, ventilazione e gestione microclima

**Misure implicite**
- ventilazione / ombreggiamento estivo
- eventuale supporto termico leggero per transizione stagionale
- scelta accurata del periodo operativo realistico

### 2. Tubular PBR solare
**Lettura:** plausibile ma più sensibile agli estremi climatici.

**Vantaggi climatici**
- sfrutta bene la radiazione locale in primavera-estate
- compatibile con posizionamento premium / qualità / controllo maggiore rispetto a sistemi aperti

**Criticità climatiche**
- luglio-agosto: rischio forte di surriscaldamento del fluido
- inverno: radiazione e temperatura troppo basse per una resa convincente senza supporto
- richiede più attenzione a dissipazione termica e gestione operativa

**Misure implicite**
- raffrescamento / dissipazione / ombreggio nei picchi estivi
- possibile stagionalità forte se si evita complessità energetica

### 3. Indoor PBR
**Lettura:** climaticamente il più robusto, economicamente il più penalizzato.

**Vantaggi climatici**
- quasi indipendente dal clima esterno
- massima continuità annuale potenziale
- minimizza la variabile meteo nella produttività

**Criticità**
- il vantaggio climatico si paga in CAPEX/OPEX
- il clima locale favorevole da aprile a settembre riduce il vantaggio relativo di un indoor rispetto a soluzioni solari/serra

**Conclusione pratica**
- il clima locale non obbliga all'indoor
- l'indoor resta una scelta da giustificare per qualità/controllo, non per semplice necessità climatica

---

## Decisioni abilitate da questa nota

1. **Il sito è compatibile con una traiettoria serra semi-chiusa o tubular solare**, almeno come architetture da approfondire seriamente.
2. **La stagionalità è reale**: senza interventi energetici, il nucleo produttivo veramente favorevole sembra concentrarsi in primavera-estate.
3. **Il rischio principale non è solo il freddo invernale, ma anche il caldo estivo** nei sistemi solari/serra.
4. **L'indoor non sembra imposto dal clima locale**: va valutato solo se vince per posizionamento, controllo o strategia premium.

---

## Gap aperti
- Recuperare, in una fase successiva, una serie climatica più lunga (10+ anni) per maggiore robustezza decisionale
- Aggiungere eventualmente umidità relativa media da fonte dedicata
- Integrare dati microclimatici reali del sito finale se la localizzazione operativa diventa più precisa
- Stimare l'effetto pratico del microclima di serra rispetto all'esterno

---

## Valutazione del task
Per il piano di aprile questo task può essere considerato **quasi chiuso** come baseline decisionale climatica, perché ora esiste:
- una nota dedicata
- più fonti combinate
- una tabella sintetica mensile
- una lettura pratica orientata alle architetture in valutazione

Per chiuderlo del tutto in modo forte, il passo successivo ideale sarebbe aggiungere una mini-sezione su **umidità relativa** e una verifica con una seconda fonte numerica indipendente per la radiazione.
