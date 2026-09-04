"""Chargement et validation de la configuration (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
TIMEZONE = ZoneInfo("Europe/Paris")

load_dotenv(ROOT / ".env")


class ConfigError(RuntimeError):
    """Une variable d'environnement obligatoire est absente ou invalide."""


def _optional_int(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ConfigError(f"{name} doit être un identifiant Discord (chiffres uniquement), reçu : {raw!r}")
    return int(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    tmdb_api_key: str
    cinema_channel_id: int | None
    guild_id: int | None
    tmdb_language: str

    @classmethod
    def load(cls) -> "Settings":
        token = (os.getenv("DISCORD_TOKEN") or "").strip()
        if not token:
            raise ConfigError(
                "DISCORD_TOKEN est vide. Crée un fichier .env à la racine du projet "
                "contenant DISCORD_TOKEN=<le token du bot>."
            )
        return cls(
            token=token,
            tmdb_api_key=(os.getenv("TMDB_API_KEY") or "").strip(),
            cinema_channel_id=_optional_int("CINEMA_CHANNEL_ID"),
            guild_id=_optional_int("GUILD_ID"),
            tmdb_language=(os.getenv("TMDB_LANGUAGE") or "fr-FR").strip(),
        )


# Cinémas proposés pour les séances : clé interne -> nom affiché.
LIEUX: dict[str, str] = {
    "palais": "Le Palais des Cerises",
    "operaims": "Opéraims",
    "pathe": "Pathé Thillois",
}
LIEU_PAR_DEFAUT = "palais"

# Emoji utilisé pour s'inscrire à une séance.
EMOJI_INSCRIPTION = "🎟️"
