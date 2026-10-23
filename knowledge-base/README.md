# Knowledge base

Markdown documents that OpsPilot attaches to investigations, and later indexes into `pgvector` for semantic search.

| Folder | Contents |
|---|---|
| `runbooks/` | Step-by-step operational procedures for known failure modes |
| `troubleshooting/` | Symptom-oriented guides ("checkout freezes", "slow dashboard") |
| `architecture/` | How ShopStream services work and depend on each other |

Each document starts with a small front-matter block (`title`, `service`, `tags`) used for matching.
