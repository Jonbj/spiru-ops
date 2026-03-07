"""ui/copilot.py — spiru-ops (documented version)

This file is part of the spiru-ops project, which builds a Spirulina/Arthrospira
knowledge base and a RAG Copilot.

The repository is intentionally documented with verbose comments so that:
- humans can quickly understand intent and invariants
- AI tools (agents, refactoring assistants) can reason about the code safely

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

with st.sidebar:
    st.header("Settings")
    focus = st.text_input("Focus (optional)", placeholder="e.g. luce_e_led")
    default_topk = int(env("COPILOT_TOPK", 10))
    topk = st.slider("TopK evidence", 3, 20, default_topk)
    st.caption("TopK default is configured via .env; slider is for evidence preview only.")
    save_to_spec = st.checkbox("Append answer to living_spec.md", value=True)

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
f = (focus or "").strip() or None

if run_preview:
    if not q:
        st.warning("Inserisci una domanda prima di fare il preview.")
    else:
        ev = retrieve(q, focus=f, topk=topk)
        st.subheader("Evidence preview")
        if not ev:
            st.info("Nessuna evidence trovata.")
        for e in ev:
            title = getattr(e, "title", "(no title)")
            score = getattr(e, "score", None)
            focus_name = getattr(e, "focus", "unknown")
            url = getattr(e, "url", None)
            text = getattr(e, "text", "") or ""

            label = f"[{e.n}] {title}"
            if score is not None:
                label += f" — score {score:.3f}"
            label += f" — focus: {focus_name}"

            with st.expander(label, expanded=(getattr(e, "n", 0) == 1)):
                if url:
                    st.markdown(f"**URL:** {url}")
                st.write(text)

if run_copilot:
    if not q:
        st.warning("Inserisci una domanda prima di lanciare Copilot.")
    else:
        with st.spinner("Calling cloud LLM with retrieved evidence..."):
            out = ask_copilot(q, focus=f)

        st.subheader("Copilot answer")
        answer = (out or {}).get("answer", "")
        if answer:
            st.markdown(answer)
        else:
            st.error("Risposta vuota (controlla i log e la configurazione API).")

        if save_to_spec and answer:
            append_to_living_spec(answer)
            st.success("Appended to storage/artifacts/living_spec.md")