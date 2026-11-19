"""Prepare redistributable offline assets for the characters in the vocabulary."""
import csv
import io
import tarfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
assets = ROOT / "assets"
assets.mkdir(exist_ok=True)
(assets / "characters").mkdir(exist_ok=True)
with (ROOT / "data/vocabulary.tsv").open(encoding="utf-8-sig") as source:
    chars = set("".join(row["hanzi"] for row in csv.DictReader(source, delimiter="\t")))

for name, version in [("hanzi-writer", "3.7.3"), ("hanzi-writer-data", "2.0.1")]:
    with urlopen(f"https://registry.npmjs.org/{name}/-/{name}-{version}.tgz", timeout=30) as response:
        with tarfile.open(fileobj=io.BytesIO(response.read()), mode="r:gz") as archive:
            for member in archive:
                path = Path(member.name)
                if not member.isfile():
                    continue
                if name == "hanzi-writer" and path.name == "hanzi-writer.min.js":
                    target = assets / path.name
                elif name == "hanzi-writer-data" and path.suffix == ".json" and path.stem in chars:
                    target = assets / "characters" / path.name
                elif path.name.lower().startswith(("license", "copying", "arphic")):
                    target = assets / (name + "-" + path.name)
                else:
                    continue
                target.write_bytes(archive.extractfile(member).read())
print("Файлов иероглифов:", len(list((assets / "characters").glob("*.json"))))

