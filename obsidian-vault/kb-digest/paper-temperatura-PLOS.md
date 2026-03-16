# Paper: Crescita Spirulina a Diverse Temperature — PLOS ONE 2025

## Riferimento
**"Growth of Spirulina spp. at different temperatures and their impact on pigment production, oxidants and antioxidants profile"**
PLOS ONE, 24 febbraio 2025
DOI: 10.1371/journal.pone.0313350
URL: https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0313350

## Dato chiave — INSOLITO rispetto alla letteratura classica

| Temperatura | Crescita (OD) | Note |
|---|---|---|
| 15°C | bassa | limite inferiore |
| **20°C** | **0.85 — MASSIMA** | ✅ ottimale secondo questo studio |
| 25°C | 0.64 | buona |
| 30°C | inferiore a 20°C | convenzionalmente indicata come ottimale |

> La letteratura classica indica 30–35°C come ottimale.
> Questo studio 2025 inverte l'indicazione: **massima crescita a 20°C**.

## Implicazione per il progetto
La cantina a ~18°C è molto più vicina all'ottimale di quanto si pensasse.
Riscaldamento attivo potrebbe non essere necessario, specialmente con ceppo adattato.

## Note pipeline
- Trovato in KB nel focus `gas_management_o2_stripping_kla` (focus sbagliato)
- Fix applicato in discover.py: ora future run indicizzeranno paper temperatura nel focus corretto `process_control_setpoints_ph_co2_temp`

## Collegato a
- [[coltura/setpoint-operativi]]
- [[coltura/setup-damigiana-50L]]
