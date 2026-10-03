"""Stage the download payload the dashboard's Data & Sources tab links to.

app/index.html has always linked to downloads/socal_fishing_dataset.xlsx, downloads/*.md and
downloads/csv/*.csv, but the deploy bundle only ships the app/ folder, so those links resolved to
nothing. This copies the workbook, the two docs and every CSV (original tables plus the new
extended-range tables) into app/downloads so the links actually work.

Idempotent. Run after gen_extended_docs.py.
"""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("SOCAL_ROOT", os.path.dirname(HERE))
DEST = os.path.join(ROOT, "app", "downloads")


def main():
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(os.path.join(DEST, "csv"), exist_ok=True)
    n = 0
    for rel in ["dataset/socal_fishing_dataset.xlsx", "docs/SOURCE_REGISTRY.md",
                "docs/DATA_DICTIONARY.md"]:
        src = os.path.join(ROOT, rel)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DEST, os.path.basename(rel)))
            n += 1
    csv_src = os.path.join(ROOT, "dataset", "csv")
    for f in sorted(os.listdir(csv_src)):
        if f.endswith(".csv"):
            shutil.copy2(os.path.join(csv_src, f), os.path.join(DEST, "csv", f))
            n += 1
        elif f in {"source_status.json", "extended_meta.json"}:
            shutil.copy2(os.path.join(csv_src, f), os.path.join(DEST, "csv", f))
            n += 1
    mb = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DEST) for f in fs) / 1e6
    print(f"staged {n} files into app/downloads ({mb:.2f} MB)")


if __name__ == "__main__":
    main()
