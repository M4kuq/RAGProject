from __future__ import annotations

import argparse

from app.db.session import SessionLocal
from app.services.seed import seed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic local/CI demo data.")
    parser.add_argument("--skip-document-indexing", action="store_true")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        seed(db, index_documents=not args.skip_document_indexing)


if __name__ == "__main__":
    main()
