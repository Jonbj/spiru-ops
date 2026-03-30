# Gantt — Piano Operativo Algavitae

Questo file è pensato per Obsidian con supporto Mermaid.

```mermaid
gantt
    title Algavitae Piano operativo verso GO NO GO
    dateFormat  YYYY-MM-DD
    axisFormat  %d/%m
    section Fase 1 Esplorazione
    Budget massimo investibile         :m1, 2026-03-30, 3d
    Registro decisionale evidenze      :m2, 2026-03-30, 3d
    Dati climatici Marche Fermo        :m3, 2026-03-30, 5d
    Digest KB focus prioritari         :m4, 2026-03-31, 5d
    Mappa competitor Italia            :m5, 2026-04-01, 19d
    Lista contatti cliente             :m6, 2026-04-02, 4d
    Consulente regolatorio             :m7, 2026-04-03, 4d
    Prime interviste clienti           :m8, 2026-04-08, 12d
    Chiamata produttori operatori      :m9, 2026-04-09, 11d
    Preventivi iniziali                :m10, 2026-04-14, 6d
    Memo normativo v0.1                :m11, 2026-04-20, 5d
    Customer discovery v0.1            :m12, 2026-04-20, 5d
    Financial model v0.1               :m13, 2026-04-21, 5d
    Allineare artefatti aprile         :m14, 2026-04-27, 3d
    Generare piano dettagliato maggio  :m15, 2026-04-29, 2d
    Decisione M1                       :milestone, ms1, 2026-04-26, 0d
    section Fase 2 Validazione
    Preventivi aggiuntivi              :n1, 2026-04-27, 13d
    Interviste clienti round 2         :n2, 2026-04-28, 15d
    Stima workload operativo           :n3, 2026-04-28, 19d
    Sopralluogo sito 1                 :n4, 2026-05-04, 7d
    Decisione food cosmetic            :milestone, ms2, 2026-05-10, 0d
    Decisione fresco secco             :milestone, ms3, 2026-05-10, 0d
    Financial model scenari            :n5, 2026-05-04, 14d
    Sopralluogo sito 2                 :n6, 2026-05-11, 7d
    Decisione architettura             :milestone, ms4, 2026-05-17, 0d
    Pipeline clienti v0.1              :n7, 2026-05-18, 7d
    Workload model v0.1                :n8, 2026-05-18, 7d
    Allineare artefatti maggio         :n9, 2026-05-25, 4d
    Generare piano dettagliato giugno  :n10, 2026-05-28, 3d
    Decisione M2                       :milestone, ms5, 2026-05-24, 0d
    section Fase 3 Business case
    Business plan v1                   :p1, 2026-05-25, 21d
    Modello finanziario v1             :p2, 2026-05-25, 21d
    Grant scan leggero                 :p3, 2026-05-26, 13d
    Interviste finali                  :p4, 2026-05-26, 18d
    Chiamata fiscalista                :p5, 2026-06-02, 11d
    Stress test BP                     :p6, 2026-06-15, 6d
    Piano 90 giorni post GO            :p7, 2026-06-15, 6d
    Allineare artefatti giugno         :p8, 2026-06-22, 4d
    Generare piano dettagliato luglio  :p9, 2026-06-25, 3d
    Decisione GO NO GO                 :milestone, ms6, 2026-06-21, 0d
    Ultimo slittamento                 :milestone, ms7, 2026-07-05, 0d

    section Fase 4 Avvio operativo
    Setup legale acquisti              :q1, 2026-07-06, 19d
    Setup produttivo                   :q2, 2026-07-20, 26d
    Allineare artefatti luglio         :q3, 2026-07-27, 4d
    Generare piano dettagliato agosto  :q4, 2026-07-29, 3d
    Lancio commerciale                 :q5, 2026-08-10, 22d
    Allineare artefatti agosto         :q6, 2026-08-24, 4d
    Prima vendita                      :milestone, ms8, 2026-08-31, 0d

    section Cadenze fisse
    Check in settimanali               :r1, 2026-04-03, 84d
    Azioni campo obbligatorie          :r2, 2026-04-01, 86d
```

## Nota d'uso
- Apri questo file in Obsidian in modalità preview per vedere il diagramma.
- Le decisioni M1, M2 e GO/NO-GO sono i veri gate del piano.
- A fine di ogni mese operativo, aggiorna gli artefatti di progetto e genera il piano dettagliato del mese successivo.
