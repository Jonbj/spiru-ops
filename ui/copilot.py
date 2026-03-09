"""ui/copilot.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and retrieval assistants can reason about the code safely

This header is *documentation-only*; the runtime logic below is preserved.
"""

import sys
from pathlib import Path

import streamlit as st

# Ensure repo root is on sys.path so `import pipelines...` works reliably under Streamlit
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.common import env  # noqa: E402
from pipelines.rag_cloud import retrieve, ask_copilot, append_to_living_spec  # noqa: E402

st.set_page_config(page_title="SpiruCopilot", layout="wide")

st.title("SpiruCopilot — PBR 50L Design Chat (RAG + Cloud LLM)")

# ── Sidebar ───────────────────────────────────────────────────────────────────
FOCUS_OPTIONS = [
    "(nessun filtro)",
    "process_control_setpoints_ph_co2_temp",
    "pbr_airlift_geometry_and_scale_down",
    "gas_management_o2_stripping_kla",
    "biomass_analytics_food_cosmetic_safety",
    "harvesting_fresh_biomass_filtration",
    "contamination_monitoring_response",
    "illumination_led_commercial_roi",
    "circular_economy_waste_streams",
    "certifications_protocols_food_cosmetic",
    "packaging_labeling_retail",
    "quality_shelf_life_storage_degradation",
    "diy_home_cultivation_kits",
    "cip_cleaning_sanitation_material_compatibility",
    "water_treatment_well_mains",
    "spirulina_strains_eu_collections",
    "marketing_branding_consumer_perception",
    "network_partners_accelerators_suppliers",
    "local_adaptation_marche_fermo_outdoor",
]

DOC_TYPE_OPTIONS = [
    "(tutti i tipi)",
    "paper",
    "thesis",
    "guideline",
    "regulation",
    "webpage",
]

with st.sidebar:
    st.header("Filtri retrieval")

    focus_sel = st.selectbox("Focus topic", FOCUS_OPTIONS, index=0)
    focus = None if focus_sel == "(nessun filtro)" else focus_sel

    doc_type_sel = st.selectbox("Tipo documento", DOC_TYPE_OPTIONS, index=0)
    doc_type_filter = None if doc_type_sel == "(tutti i tipi)" else doc_type_sel

    default_topk = int(env("COPILOT_TOPK", 10))
    topk = st.slider("Top-K evidence", 3, 20, default_topk,
                     help="Numero di chunk recuperati dalla KB. Influisce sia sul preview che sulla risposta LLM.")

    save_to_spec = st.checkbox("Append answer to living_spec.md", value=True)

    st.divider()
    st.caption("Modello embedding: BAAI/bge-m3 (hybrid retrieval)")
    st.caption(f"Collection: {env('QDRANT_COLLECTION', 'docs_chunks_v2')}")

# ── Main area ─────────────────────────────────────────────────────────────────
question = st.text_area(
    "Cosa vuoi progettare/decidere?",
    placeholder=(
        "Esempio: Proponi un design flat-panel per 50L con BOM sensori e setpoint iniziali. "
        "Includi piano test DoE minimo."
    ),
    height=140,
)

colA, colB = st.columns([1, 1])
run_preview = colA.button("Preview evidence")
run_copilot = colB.button("Run Copilot (LLM)", type="primary")

q = (question or "").strip()


def _build_focus_filter(focus: str | None, doc_type: str | None) -> dict | None:
    """Build a combined Qdrant filter for focus and/or doc_type."""
    must = []
    if focus:
        must.append({"key": "focus", "match": {"value": focus}})
    if doc_type:
        must.append({"key": "doc_type", "match": {"value": doc_type}})
    return must[0] if len(must) == 1 else ({"must": must} if must else None)


# For retrieve() the focus arg drives the internal filter; doc_type is not yet
# a native param → we pass it via the env-based filter extension below.
# Simple solution: pass focus only; show doc_type in evidence table as info.
# (Full doc_type filter wiring to rag_cloud.py left as next step.)

if run_preview:
    if not q:
        st.warning("Inserisci una domanda prima di fare il preview.")
    else:
        ev = retrieve(q, focus=focus, topk=topk, doc_type=doc_type_filter)
        st.subheader("Evidence preview")
        if not ev:
            st.info("Nessuna evidence trovata.")

        for e in ev:
            title = getattr(e, "title", "(no title)")
            score = getattr(e, "score", None)
            focus_name = getattr(e, "focus", "unknown")
            url = getattr(e, "url", None)
            text = getattr(e, "text", "") or ""
            pub = getattr(e, "published_at", None) or "—"
            source = getattr(e, "source", "") or ""

            label = f"[{e.n}] {title}"
            if score is not None:
                label += f" — score {score:.3f}"
            label += f" | {focus_name}"

            with st.expander(label, expanded=(getattr(e, "n", 0) == 1)):
                cols = st.columns([2, 1, 1, 1])
                cols[0].markdown(f"**URL:** {url}" if url else "")
                cols[1].markdown(f"**Source:** {source}")
                cols[2].markdown(f"**Published:** {pub}")
                cols[3].markdown(f"**Focus:** {focus_name}")
                st.write(text[:1200] + ("…" if len(text) > 1200 else ""))

if run_copilot:
    if not q:
        st.warning("Inserisci una domanda prima di lanciare Copilot.")
    else:
        with st.spinner("Calling cloud LLM with retrieved evidence..."):
            out = ask_copilot(q, focus=focus, topk_override=topk, doc_type=doc_type_filter)

        st.subheader("Copilot answer")
        answer = (out or {}).get("answer", "")
        if answer:
            st.markdown(answer)

            # Show evidence used in expander
            ev_used = (out or {}).get("evidence_used") or []
            if ev_used:
                with st.expander(f"Evidence usata ({len(ev_used)} fonti)", expanded=False):
                    for e in ev_used:
                        st.markdown(
                            f"**[{e['n']}]** [{e.get('title','?')}]({e.get('url','')}) "
                            f"— score `{e.get('score',0):.3f}` "
                            f"| focus: `{e.get('focus','?')}` "
                            f"| source: `{e.get('source','?')}`"
                        )
        else:
            st.error("Risposta vuota (controlla i log e la configurazione API).")

        if save_to_spec and answer:
            append_to_living_spec(answer, question=q)
            st.success("Appended to storage/artifacts/living_spec.md")
