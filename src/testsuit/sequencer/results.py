"""Results datapack (README section 10.1): the run folder packed with the layout of the datapack.

- ``data/<component>/input/``: the inputs, unchanged; ``data/<component>/output/``: what was collected;
- ``logs/``: the log of every step; ``selftest.yml``, ``run.yml`` and the completed ``baseline.yml``.

The BagIt manifests are recomputed over everything. Files over ``large_files.threshold`` and
already compressed formats are stored without compression; zip64 is used as needed. With
``large_files.mode: external``, files over the threshold stay next to the zip, in
``<name>_results.data/``, listed in BagIt's ``fetch.txt``.
"""
from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

from testsuit.datatools.datapack.datapack_tools import _sha256, write_bag

COMPRESSED = (".gz", ".zst", ".xz", ".bz2", ".zip", ".7z", ".png", ".jpg", ".jpeg", ".mp4", ".h5", ".parquet")


def pack_results(bag: Path, output_dir: Path, name: str, threshold: int, mode: str = "zip",
                 software: str = "testsuit sequencer") -> Path:
    """Write the results zip of a run folder; return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{name}.zip"
    external_dir = output_dir / f"{name}.data"
    for stale in ("fetch.txt",):
        (bag / stale).unlink(missing_ok=True)
    tsq_tmp = bag / ".tsq"
    if tsq_tmp.exists():                              # process control files of steps run on the sequencer host
        shutil.rmtree(tsq_tmp, ignore_errors=True)
    write_bag(bag, name, software)

    external: list[tuple[Path, str]] = []
    if mode == "external":
        for path in sorted((bag / "data").rglob("*")):
            if path.is_file() and path.stat().st_size > threshold:
                external.append((path, path.relative_to(bag).as_posix()))
        if external:
            lines = [f"{external_dir.name}/{rel} {path.stat().st_size} {rel}\n" for path, rel in external]
            (bag / "fetch.txt").write_text("".join(lines), encoding="utf-8")
            # fetch.txt is a tag file: recompute the tag manifest with it
            tags = sorted(p for p in bag.rglob("*") if p.is_file() and p.relative_to(bag).parts[0] != "data"
                          and p.name != "tagmanifest-sha256.txt")
            (bag / "tagmanifest-sha256.txt").write_text(
                "".join(f"{_sha256(p)}  {p.relative_to(bag).as_posix()}\n" for p in tags), encoding="utf-8")

    tmp = zip_path.with_suffix(".zip.part")
    external_rel = {rel for _, rel in external}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr(name + "/", "")
        for path in sorted(bag.rglob("*")):
            rel = path.relative_to(bag).as_posix()
            arcname = f"{name}/{rel}"
            if path.is_dir():
                archive.writestr(arcname + "/", "")
            elif rel not in external_rel:
                stored = path.stat().st_size > threshold or path.suffix.lower() in COMPRESSED
                archive.write(path, arcname, compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    os.replace(tmp, zip_path)
    for path, rel in external:
        target = external_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return zip_path
