<!--
SPIRU-OPS PROMPT TEMPLATE (documented)

These prompt files are used by the RAG Copilot (pipelines/rag_cloud.py + ui/copilot.py).

Key design goals:
- "Evidence-only" answering: the model must ground statements in retrieved snippets.
- Consistent citation format: [1], [2], ...
- Spirulina-centric behavior: prefer Spirulina/Arthrospira evidence; avoid generic microalgae.

If the Copilot outputs many TBDs, it usually means:
- retrieval returned generic/irrelevant documents
- evidence formatting was weak (snippets too short or boilerplate)
- prompt constraints prevented extrapolation (by design)

This header is documentation only; the prompt content below is what the model sees.
-->

Project state (living spec excerpt):
{living_spec_excerpt}

User request:
{question}

Retrieved evidence snippets (each has an id and URL):
{evidence}

Now produce the structured answer described in the system prompt.
Remember: do not invent numeric values; mark TBD + propose experiments.