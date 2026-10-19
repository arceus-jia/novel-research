"""Build the standalone skill archive from a clean, explicit file set."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


root = Path(__file__).resolve().parents[1]
skill = root / "skills" / "novel-research"
files = [skill / "SKILL.md"]
files += sorted((skill / "scripts").glob("*.py"))
files += sorted((skill / "references").glob("*.md"))
files += sorted(path for path in (skill / "assets").rglob("*") if path.is_file())
archive = root / "dist" / "novel-research.zip"
archive.parent.mkdir(exist_ok=True)
with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
    for path in files:
        output.write(path, path.relative_to(skill.parent).as_posix())
print(f"{archive} ({len(files)} files, {archive.stat().st_size} bytes)")
