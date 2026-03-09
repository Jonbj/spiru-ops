"""scripts/gen_pbr_spec.py — Genera scheda tecnica PDF per preventivo fornitore PMMA."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from datetime import date
import pathlib

OUT = pathlib.Path("storage/artifacts/scheda_tecnica_PBR_PMMA.pdf")
OUT.parent.mkdir(parents=True, exist_ok=True)

# ── colori ────────────────────────────────────────────────────────────────────
VERDE   = colors.HexColor("#2E7D32")
VERDE_L = colors.HexColor("#E8F5E9")
GRIGIO  = colors.HexColor("#546E7A")
GRIGIO_L= colors.HexColor("#ECEFF1")
BIANCO  = colors.white
NERO    = colors.HexColor("#212121")

doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    topMargin=18*mm, bottomMargin=18*mm,
    leftMargin=18*mm, rightMargin=18*mm,
    title="Scheda Tecnica PBR Airlift PMMA",
    author="Spiru-Ops",
)

styles = getSampleStyleSheet()

def sty(name, **kw):
    return ParagraphStyle(name, parent=styles["Normal"], **kw)

S_TITLE   = sty("title",   fontSize=20, textColor=VERDE,  spaceAfter=2*mm,  leading=24, fontName="Helvetica-Bold")
S_SUB     = sty("sub",     fontSize=11, textColor=GRIGIO, spaceAfter=6*mm,  leading=14)
S_H1      = sty("h1",      fontSize=12, textColor=BIANCO, spaceAfter=0,     leading=15, fontName="Helvetica-Bold")
S_BODY    = sty("body",    fontSize=9,  textColor=NERO,   spaceAfter=2*mm,  leading=13)
S_SMALL   = sty("small",   fontSize=8,  textColor=GRIGIO, spaceAfter=1*mm,  leading=11)
S_NOTE    = sty("note",    fontSize=8,  textColor=GRIGIO, spaceAfter=2*mm,  leading=11, leftIndent=4*mm)
S_FOOTER  = sty("footer",  fontSize=7,  textColor=GRIGIO, alignment=TA_CENTER)
S_RIGHT   = sty("right",   fontSize=8,  textColor=GRIGIO, alignment=TA_RIGHT)

def section(title):
    """Intestazione sezione su sfondo verde."""
    data = [[Paragraph(title, S_H1)]]
    t = Table(data, colWidths=[174*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), VERDE),
        ("TOPPADDING",    (0,0), (-1,-1), 3*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3*mm),
        ("LEFTPADDING",   (0,0), (-1,-1), 4*mm),
        ("RIGHTPADDING",  (0,0), (-1,-1), 4*mm),
        ("ROUNDEDCORNERS", [3]),
    ]))
    return [t, Spacer(1, 3*mm)]

def param_table(rows, col_w=None):
    """Tabella parametri a due colonne (parametro | valore)."""
    if col_w is None:
        col_w = [80*mm, 94*mm]
    styled = []
    for i, (k, v) in enumerate(rows):
        bg = GRIGIO_L if i % 2 == 0 else BIANCO
        styled.append([
            Paragraph(f"<b>{k}</b>", sty(f"pk{i}", fontSize=9, textColor=NERO, leading=12)),
            Paragraph(str(v),        sty(f"pv{i}", fontSize=9, textColor=NERO, leading=12)),
        ])
    t = Table(styled, colWidths=col_w)
    ts = TableStyle([
        ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#B0BEC5")),
        ("TOPPADDING",    (0,0), (-1,-1), 2.5*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 2.5*mm),
        ("LEFTPADDING",   (0,0), (-1,-1), 3*mm),
        ("RIGHTPADDING",  (0,0), (-1,-1), 3*mm),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ])
    for i in range(0, len(rows), 2):
        ts.add("BACKGROUND", (0,i), (-1,i), GRIGIO_L)
    t.setStyle(ts)
    return [t, Spacer(1, 4*mm)]

def note(txt):
    return [Paragraph(f"⚠ {txt}", S_NOTE), Spacer(1, 2*mm)]

# ─────────────────────────────────────────────────────────────────────────────
story = []

# ── INTESTAZIONE ──────────────────────────────────────────────────────────────
story += [
    Paragraph("RICHIESTA DI PREVENTIVO", S_TITLE),
    Paragraph("Fotobioreattore Airlift a Colonna in PMMA — 30 L pilota", S_SUB),
    HRFlowable(width="100%", thickness=1, color=VERDE, spaceAfter=4*mm),
    Table([
        [Paragraph("<b>Committente:</b> Progetto Spiru-Ops (R&D Spirulina)", S_BODY),
         Paragraph(f"<b>Data:</b> {date.today().strftime('%d/%m/%Y')}", S_RIGHT)],
        [Paragraph("<b>Oggetto:</b> Preventivo fornitura colonna PBR in PMMA trasparente con accessori", S_BODY),
         Paragraph("<b>Rif.:</b> PBR-PMMA-30L-v1", S_RIGHT)],
    ], colWidths=[120*mm, 54*mm]),
    Spacer(1, 6*mm),
]

# ── 1. DESCRIZIONE GENERALE ───────────────────────────────────────────────────
story += section("1 · Descrizione generale")
story += [Paragraph(
    "Si richiede la realizzazione di un <b>fotobioreattore a colonna (PBR) con airlift interno</b> "
    "per la coltivazione di microalghe (<i>Arthrospira platensis</i> — Spirulina). "
    "Il reattore è destinato a uso di laboratorio/pilota (non pressurizzato). "
    "Tutti i componenti a contatto con il mezzo di coltura devono essere in PMMA (polimetilmetacrilato) "
    "trasparente, grado alimentare/tecnico, privo di plastificanti.", S_BODY),
    Spacer(1, 4*mm),
]

# ── 2. DIMENSIONI COLONNA ─────────────────────────────────────────────────────
story += section("2 · Dimensioni colonna principale")
story += param_table([
    ("Diametro esterno (DE)",         "250 mm"),
    ("Diametro interno (DI)",         "240 mm  (spessore parete 5 mm)"),
    ("Altezza utile (corpo cilindrico)","1 100 mm"),
    ("Volume di lavoro",              "≈ 30 L"),
    ("Forma fondo",                   "Piatto, con O-ring seat Ø250 mm"),
    ("Forma coperchio",               "Piatto rimovibile, con O-ring seat Ø250 mm"),
    ("Tolleranza diametro",           "± 1 mm"),
    ("Tolleranza altezza",            "± 5 mm"),
    ("Finitura superfici interne",    "Lucida (Ra ≤ 0,8 µm) — no rigature"),
    ("Finitura superfici esterne",    "Lucida — trasparenza ≥ 92%"),
])

# ── 3. DRAFT TUBE (AIRLIFT INTERNO) ──────────────────────────────────────────
story += section("3 · Draft tube interno (riser airlift)")
story += [Paragraph(
    "Il draft tube è un tubo coassiale interno alla colonna che separa il <b>riser</b> "
    "(zona di risalita delle bolle d'aria) dall'<b>annulus/downcomer</b> (zona di discesa del liquido). "
    "Deve essere posizionato e fissato coassialmente al centro della colonna.", S_BODY),
    Spacer(1, 3*mm),
]
story += param_table([
    ("Diametro esterno draft tube",   "150 mm"),
    ("Diametro interno draft tube",   "140 mm  (spessore 5 mm)"),
    ("Altezza draft tube",            "900 mm  (posizionato a 50 mm dal fondo)"),
    ("Clearance inferiore (bottom)",  "50 mm  (tra fondo colonna e base draft tube)"),
    ("Clearance superiore (top)",     "150 mm  (tra sommità draft tube e coperchio)"),
    ("Fissaggio",                     "3 distanziali radiali in PMMA da 5 mm, incollati (Acrifix)"),
    ("Finitura",                      "Lucida su entrambe le superfici"),
])

# ── 4. APERTURE E PASSAGGI ────────────────────────────────────────────────────
story += section("4 · Aperture, flange e passaggi (da eseguire sul corpo e sul coperchio)")

story += [Paragraph("<b>4a — Fondo (piastra inferiore)</b>", S_BODY), Spacer(1,1*mm)]
story += param_table([
    ("Passaggio sparger (centro riser)",  "Foro Ø 12 mm filettato M12, centrato — ingresso aria"),
    ("Scarico/drenaggio (annulus)",       "Foro Ø 25 mm filettato 1\" BSP, a 15 mm dalla parete esterna"),
    ("Fissaggio fondo a colonna",         "Flangia incollata + 4 viti M6 in PP/PVDF con O-ring silicone Ø250"),
], col_w=[95*mm, 79*mm])

story += [Paragraph("<b>4b — Coperchio (piastra superiore, rimovibile)</b>", S_BODY), Spacer(1,1*mm)]
story += param_table([
    ("Uscita gas / sfiato",               "Foro Ø 20 mm filettato 3/4\" BSP, zona annulus"),
    ("Porta sonda pH",                    "Foro Ø 12 mm, posizione radiale 45°"),
    ("Porta sonda temperatura / DO",      "Foro Ø 12 mm, posizione radiale 135°"),
    ("Ingresso nutrienti / inoculo",      "Foro Ø 10 mm filettato M10, zona annulus"),
    ("Campionamento",                     "Foro Ø 10 mm filettato M10, zona riser"),
], col_w=[95*mm, 79*mm])

story += note(
    "Le filettature devono essere eseguite con maschio metrico su PMMA. "
    "Non utilizzare inserti in metallo salvo accordo esplicito. "
    "Preferire raccordi passanti in PP o PVDF per le porte sensori."
)

# ── 5. MATERIALE ─────────────────────────────────────────────────────────────
story += section("5 · Specifiche materiale PMMA")
story += param_table([
    ("Materiale",                     "PMMA estruso o colato, trasparente, incolore"),
    ("Grado",                         "Tecnico/alimentare — privo di plastificanti e BPA"),
    ("Stabilizzazione UV",            "Preferibile UV-stabilizzato (per uso con illuminazione LED)"),
    ("Resistenza chimica richiesta",  "pH 9–10.5 (mezzo Zarrouk: NaHCO₃, Na₂CO₃, NaNO₃, K₂HPO₄)"),
    ("Temperatura operativa",         "25–38 °C  (continua)"),
    ("Pressione operativa",           "Atmosferica + colonna d'acqua ≤ 0.1 bar — non pressurizzato"),
    ("Trasparenza minima",            "≥ 92% (lunghezza d'onda 400–700 nm)"),
    ("Incollaggio/giunzioni",         "Acrifix 192 o equivalente certificato per PMMA"),
])
story += note(
    "Si richiede di specificare in offerta il fornitore del semilavorato PMMA "
    "(es. Evonik Plexiglas®, Arkema Altuglas®, Röhm) e il tipo (estruso/colato)."
)

# ── 6. FORNITURA RICHIESTA ────────────────────────────────────────────────────
story += section("6 · Elenco pezzi richiesti (Bill of Materials)")

bom = [
    ["N°", "Descrizione", "Qtà", "Note"],
    ["1",  "Tubo PMMA Ø250(e)×240(i) mm H=1100 mm", "1 pz", "Corpo colonna"],
    ["2",  "Tubo PMMA Ø150(e)×140(i) mm H=900 mm",  "1 pz", "Draft tube / riser"],
    ["3",  "Disco PMMA sp. 10 mm Ø250 mm (fondo)",  "1 pz", "Piastra inferiore, foratura vedi §4a"],
    ["4",  "Disco PMMA sp. 10 mm Ø250 mm (coperchio)", "1 pz", "Piastra superiore rimovibile, foratura vedi §4b"],
    ["5",  "Flangia incollaggio fondo Ø250 mm",      "1 pz", "Anello PMMA sp. 8 mm, sede O-ring"],
    ["6",  "Distanziali radiali draft tube 5×30 mm", "3 pz", "Incollati ad Acrifix"],
    ["7",  "Lavorazione CNC aperture e filettature",  "—",   "Vedi §4a e §4b"],
    ["8",  "Collaudo visivo trasparenza e tenuta",    "—",   "Prima della spedizione"],
]

bom_t = Table(bom, colWidths=[10*mm, 80*mm, 18*mm, 66*mm])
bom_t.setStyle(TableStyle([
    ("BACKGROUND",    (0,0), (-1,0), VERDE),
    ("TEXTCOLOR",     (0,0), (-1,0), BIANCO),
    ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE",      (0,0), (-1,-1), 8),
    ("GRID",          (0,0), (-1,-1), 0.3, colors.HexColor("#B0BEC5")),
    ("TOPPADDING",    (0,0), (-1,-1), 2*mm),
    ("BOTTOMPADDING", (0,0), (-1,-1), 2*mm),
    ("LEFTPADDING",   (0,0), (-1,-1), 2*mm),
    ("RIGHTPADDING",  (0,0), (-1,-1), 2*mm),
    ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [BIANCO, GRIGIO_L]),
]))
story += [bom_t, Spacer(1, 4*mm)]

# ── 7. INFORMAZIONI RICHIESTE IN OFFERTA ──────────────────────────────────────
story += section("7 · Informazioni richieste nell'offerta")
story += [
    Paragraph("Si prega di includere nell'offerta:", S_BODY),
    Paragraph("• Prezzo unitario per ciascun articolo BOM (§6)", S_BODY),
    Paragraph("• Eventuali varianti di diametro disponibili a stock (es. Ø200 mm, Ø300 mm)", S_BODY),
    Paragraph("• Tipo e fornitore del semilavorato PMMA utilizzato", S_BODY),
    Paragraph("• Tempi di consegna stimati", S_BODY),
    Paragraph("• Disponibilità a fornire campione/coupon PMMA 100×100×5 mm per test di compatibilità chimica", S_BODY),
    Paragraph("• Possibilità di collaudo in acqua prima della spedizione", S_BODY),
    Spacer(1, 4*mm),
]

# ── 8. NOTE FINALI ────────────────────────────────────────────────────────────
story += section("8 · Note e condizioni")
story += [
    Paragraph(
        "Il progetto è in fase prototipale: le dimensioni indicate possono essere adattate "
        "sulla base delle disponibilità di stock del fornitore, previa concordare con il committente. "
        "Per qualsiasi chiarimento tecnico contattare: <b>stefano.delgobbo@gmail.com</b>.",
        S_BODY),
    Spacer(1, 6*mm),
    HRFlowable(width="100%", thickness=0.5, color=GRIGIO, spaceAfter=3*mm),
    Paragraph(
        f"Documento generato automaticamente da Spiru-Ops R&amp;D — {date.today().strftime('%d/%m/%Y')} — Rif. PBR-PMMA-30L-v1",
        S_FOOTER),
]

# ── BUILD ─────────────────────────────────────────────────────────────────────
doc.build(story)
print(f"PDF generato: {OUT}")
