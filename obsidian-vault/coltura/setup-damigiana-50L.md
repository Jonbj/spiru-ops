# Setup Damigiana 50L

## Contenitore
- Damigiana vetro trasparente 50L
- Dimensioni: corpo ~50cm diametro, altezza ~60cm
- Collo interno: ~5cm (verificato) — stick LED ci passa

## Illuminazione
- **Prodotto**: Hygger 14W Full Spectrum Submersible Stick (IP68)
- Spettro: 6500K + 455nm blu + 620nm rosso
- Timer integrato + dimmer 10 livelli
- Montaggio: stick verticale al centro, cavo dal collo

Vedi: [[hardware/LED-illuminazione]]

## Aerazione
- Pompa aria mini acquario
- Pietra porosa Ø ~25mm sul fondo
- Portata target: **0.3 vvm** = 15 L/min per 50L (vedi [[coltura/setpoint-operativi]])
- Tubetto silicone alimentare

## Tappo
- Tappo conico silicone alimentare con foro (alcofermbrew.com)
- Due passaggi: cavo LED + tubetto aria

## Sensori
- [[hardware/sensori-ESP32]] — pH (DFRobot SEN0169-V2) + EC (DFR0300 IP68) + DS18B20

## Stima produttività
- Temperatura cantina: ~18°C → crescita confermata (PLOS ONE 2025)
- Produttività attesa: ~0.05–0.10 g/L/day a 18°C (ridotta vs ottimale)
- 50L × 0.07 g/L/day = **~3.5 g/day biomassa secca**
- Raccolta consigliata: ogni 3–5 giorni in semi-batch
