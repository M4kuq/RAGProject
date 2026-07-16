from __future__ import annotations

import argparse
import os

from app.db.session import SessionLocal
from app.services.seed import seed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Seed deterministic local/CI demo data.")
    parser.add_argument("--skip-document-indexing", action="store_true")
    parser.add_argument(
        "--deployed-admin-from-env",
        action="store_true",
        help=(
            "Create only the deployed administrator using RAG_DEMO_ADMIN_EMAIL and "
            "RAG_DEMO_ADMIN_PASSWORD from the process environment."
        ),
    )
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        if args.deployed_admin_from_env:
            admin_email = os.environ.get("RAG_DEMO_ADMIN_EMAIL", "").strip()
            admin_password = os.environ.get("RAG_DEMO_ADMIN_PASSWORD", "")
            if not admin_email or not admin_password:
                parser.error(
                    "--deployed-admin-from-env requires RAG_DEMO_ADMIN_EMAIL and "
                    "RAG_DEMO_ADMIN_PASSWORD"
                )
            seed(
                db,
                index_documents=not args.skip_document_indexing,
                deployed_admin_email=admin_email,
                deployed_admin_password=admin_password,
            )
        else:
            seed(db, index_documents=not args.skip_document_indexing)


if __name__ == "__main__":
    main()
