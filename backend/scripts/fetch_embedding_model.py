"""Download the ONNX embedding model the retrieval and novelty engines use.

    python scripts/fetch_embedding_model.py

Fetches all-MiniLM-L6-v2 (the ONNX export of the same weights sentence-transformers serves) and
its tokenizer into backend/data/models/. Run once after installing dependencies.
"""

import sys
from pathlib import Path

REPO = "sentence-transformers/all-MiniLM-L6-v2"
FILES = ("tokenizer.json", "onnx/model.onnx")
DEST = Path(__file__).resolve().parents[1] / "data" / "models" / "all-MiniLM-L6-v2-onnx"


def main() -> int:
    from huggingface_hub import hf_hub_download

    DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = DEST / name
        if target.exists():
            print(f"present  {name} ({target.stat().st_size // 1024} KB)")
            continue
        path = Path(hf_hub_download(repo_id=REPO, filename=name, local_dir=str(DEST)))
        print(f"fetched  {name} ({path.stat().st_size // 1024} KB)")
    print(f"\nmodel ready in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
