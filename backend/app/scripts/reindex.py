"""Sync knowledge-base/ into the database and (re)embed whatever changed.

    python -m app.scripts.reindex

Safe to run on every start: unchanged documents embedded by the current model are skipped.
"""

from app.ai.embeddings import get_embedder
from app.core.database import SessionLocal
from app.services.knowledge import load_knowledge_base


def main() -> None:
    with SessionLocal() as db:
        stats = load_knowledge_base(db)
    print(f"reindex: {stats} (embedding model {get_embedder().name})")


if __name__ == "__main__":
    main()
