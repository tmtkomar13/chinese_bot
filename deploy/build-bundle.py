"""Build a Debian deployment archive, preserving Unicode paths on Windows."""
import argparse
import tarfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = ["trainer", "deploy", "assets", "data/vocabulary.tsv", "requirements.txt",
             "README.md", "PROJECT_STRUCTURE.md"]
    if not args.update:
        files.append("data/trainer.sqlite3")

    def exclude_cache(member):
        return None if "__pycache__" in Path(member.name).parts else member

    with tarfile.open(args.archive, "x:gz") as archive:
        for name in files:
            archive.add(root / name, arcname=name, filter=exclude_cache)


if __name__ == "__main__":
    main()
