from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest

DEFAULT_OUTPUT = Path("../artifacts/evaluation/local_accuracy_dev_v1.json")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the validated tuning-only local accuracy dataset."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    manifest = build_local_accuracy_dev_manifest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"dataset={manifest.dataset.dataset_name}")
    print(f"case_count={len(manifest.cases)}")
    print(f"content_fingerprint={manifest.content_fingerprint()}")
    print(f"corpus_fingerprint={manifest.corpus_fingerprint()}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
