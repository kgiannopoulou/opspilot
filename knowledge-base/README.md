# Knowledge base

Markdown documents that OpsPilot retrieves during investigations (RAG). On load each document is split into
heading-aligned chunks, embedded and stored in `pgvector`; investigations retrieve the most relevant passages and
cite them by section (e.g. `[RUNBOOK-payment-gateway-timeout §2]`).

| Folder | Contents |
|---|---|
| `runbooks/` | Step-by-step operational procedures for known failure modes |
| `troubleshooting/` | Symptom-oriented guides ("checkout freezes", "slow dashboard", database triage) |
| `architecture/` | How ShopStream services work and depend on each other |
| `process/` | How incidents are run: severities, roles, escalation |

Each document starts with a small front-matter block (`title`, optional `service`, `tags`). Sections are numbered
from the `##` / `###` headings, so keep one topic per heading: a section is the unit the AI cites.

After editing a file run `python -m app.scripts.reindex` (the API container does this on start). Only documents
whose content hash changed are re-chunked and re-embedded; switching the embedding model re-embeds everything.
