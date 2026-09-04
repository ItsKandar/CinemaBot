"""Persistance JSON simple, sérialisée par un verrou asyncio.

Le volume de données est minuscule (quelques séances, quelques rôles-réaction) :
un fichier JSON réécrit atomiquement suffit et reste lisible à la main.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonStore:
    def __init__(self, path: Path, default: Any = None) -> None:
        self.path = path
        self._default = {} if default is None else default
        self._lock = asyncio.Lock()
        self._data: Any = None

    @property
    def lock(self) -> asyncio.Lock:
        """Verrou à prendre pour toute séquence lecture -> modification -> écriture."""
        return self._lock

    def _read(self) -> Any:
        if self._data is not None:
            return self._data
        try:
            with self.path.open(encoding="utf-8") as fp:
                self._data = json.load(fp)
        except FileNotFoundError:
            self._data = json.loads(json.dumps(self._default))
        except json.JSONDecodeError:
            backup = self.path.with_suffix(self.path.suffix + ".corrupt")
            self.path.replace(backup)
            self._data = json.loads(json.dumps(self._default))
        return self._data

    def data(self) -> Any:
        """Données en mémoire (à modifier en place, puis appeler save())."""
        return self._read()

    def save(self) -> None:
        data = self._read()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            # mkstemp crée en 0600 : illisible depuis l'hôte quand le fichier
            # vit dans un volume de conteneur. Rien de secret ici, seulement
            # des identifiants Discord.
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise
