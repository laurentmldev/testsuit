"""Datapack importer for the ACME archive store (``importer: acme-archive`` in a datapack setup).

A stand-in for a data server: the datasource ``path`` is a folder holding one archive per dataset
version, either ``<version>.zip`` or a plain ``<version>/`` folder. The dataset ``path`` is the
version to import::

    datarepositories:
      - id: acme_data
        name: ACME bench data
        path: ~/acme/datarepo                    # where datasets are imported
        datasource:
          - id: calib_store
            name: ACME calibration store
            importer: acme-archive
            path: /shared/acme/calibrations       # the archive store
            dataset:
              - id: bench_calib
                path: _K_(bench_calib_version)    # ex: v1.2 -> /shared/acme/calibrations/v1.2.zip

The importer copies the archive, records a checksum of every file, and writes ``dataset.dico``
(``<dataset id>.version``, ``.path``, ``.source``, plus the lines of the archive's own
``dataset.dico`` if it has one), whose keys the datapack files can then use.
On the next datapack run the dataset is not imported again: its version and checksums are checked.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

from testsuit.datatools.datapack.ADataImporter import ADataImporter

MARKER_FILE = ".acme_import.json"
DICO_FILE = "dataset.dico"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AcmeArchiveImporter(ADataImporter):

    def __init__(self, targetDir: str, remotePath: str, versionId: str) -> None:
        super().__init__(targetDir, remotePath, versionId)
        self._datasetName = os.path.basename(os.path.dirname(os.path.realpath(targetDir)))

    def _source(self) -> Path | None:
        store = Path(os.path.expanduser(self._remotePath))
        for candidate in (store / f"{self._versionId}.zip", store / self._versionId):
            if candidate.exists():
                return candidate
        return None

    def _marker(self) -> dict | None:
        marker = Path(self._targetDir) / MARKER_FILE
        return json.loads(marker.read_text()) if marker.is_file() else None

    def _dataFiles(self) -> dict[str, str]:
        """Checksums of the imported files, by path relative to the dataset folder."""
        root = Path(self._targetDir)
        return {p.relative_to(root).as_posix(): _sha256(p) for p in sorted(root.rglob("*"))
                if p.is_file() and p.name not in (MARKER_FILE, DICO_FILE)}

    def retrieve(self) -> bool:
        source = self._source()
        if source is None:
            print(f"ERROR: no '{self._versionId}.zip' nor '{self._versionId}/' in ACME archive store '{self._remotePath}'")
            return False

        print(f"# ACME archive importer: {source} -> {self._targetDir}")
        target = Path(self._targetDir)
        target.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            with zipfile.ZipFile(source) as archive:
                archive.extractall(target)

        (target / MARKER_FILE).write_text(json.dumps(
            {"version": self._versionId, "source": str(source), "files": self._dataFiles()}, indent=2))

        dicoFile = target / DICO_FILE
        archiveDico = dicoFile.read_text() if dicoFile.is_file() else ""
        dicoFile.write_text(
            f"# written by the acme-archive datapack importer\n"
            f"{self._datasetName}.version={self._versionId}\n"
            f"{self._datasetName}.path={target.resolve().as_posix()}\n"
            f"{self._datasetName}.source={source.resolve().as_posix()}\n"
            + archiveDico)
        return True

    def getTag(self, testTag: bool = False) -> str | bool:
        marker = self._marker()
        if testTag:
            return marker is not None
        return marker["version"] if marker else ""

    def getChanges(self, testClean: bool = False) -> list[str] | bool:
        marker = self._marker()
        imported = marker["files"] if marker else {}
        current = self._dataFiles()
        changes = [f"M {path}" for path in imported if path in current and current[path] != imported[path]]
        changes += [f"D {path}" for path in imported if path not in current]
        changes += [f"?? {path}" for path in current if path not in imported]
        if testClean:
            if changes:
                print("\n".join(changes))
            return not changes
        return changes

    def checkVersion(self, expectedVersion: str) -> bool:
        if not self.getChanges(testClean=True):
            print(f"ERROR: dataset has local modifications: '{self._targetDir}'")
            return False
        return self.getTag() == expectedVersion
