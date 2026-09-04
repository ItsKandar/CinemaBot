"""Petit client asynchrone pour l'API TMDB (recherche + détails film)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import aiohttp

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
MOVIE_URL = "https://www.themoviedb.org/movie/{id}"


class TMDBError(RuntimeError):
    """Erreur renvoyée par TMDB ou configuration absente."""


@dataclass(slots=True)
class Movie:
    id: int
    title: str
    overview: str
    poster_url: str | None
    release_date: str | None
    runtime: int | None = None
    genres: list[str] = field(default_factory=list)

    @property
    def year(self) -> str | None:
        return self.release_date[:4] if self.release_date else None

    @property
    def url(self) -> str:
        return MOVIE_URL.format(id=self.id)

    @property
    def label(self) -> str:
        return f"{self.title} ({self.year})" if self.year else self.title

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "overview": self.overview,
            "poster_url": self.poster_url,
            "release_date": self.release_date,
            "runtime": self.runtime,
            "genres": list(self.genres),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Movie":
        return cls(
            id=int(raw["id"]),
            title=raw.get("title") or "Film inconnu",
            overview=raw.get("overview") or "",
            poster_url=raw.get("poster_url"),
            release_date=raw.get("release_date"),
            runtime=raw.get("runtime"),
            genres=list(raw.get("genres") or []),
        )

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "Movie":
        poster = payload.get("poster_path")
        genres = payload.get("genres") or []
        return cls(
            id=int(payload["id"]),
            title=payload.get("title") or payload.get("original_title") or "Film inconnu",
            overview=(payload.get("overview") or "").strip(),
            poster_url=f"{IMAGE_BASE}{poster}" if poster else None,
            release_date=(payload.get("release_date") or None),
            runtime=payload.get("runtime") or None,
            genres=[g["name"] for g in genres if isinstance(g, dict) and g.get("name")],
        )


class TMDBClient:
    def __init__(self, api_key: str, language: str = "fr-FR") -> None:
        self._api_key = api_key
        self._language = language
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        # Un token v4 est un JWT : il s'envoie en Authorization, pas en query string.
        if self._api_key.startswith("eyJ"):
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, path: str, **params: Any) -> dict[str, Any]:
        if not self.configured:
            raise TMDBError(
                "TMDB_API_KEY n'est pas renseignée dans le .env : la recherche de films est indisponible."
            )
        query = {"language": self._language, **{k: v for k, v in params.items() if v is not None}}
        if not self._api_key.startswith("eyJ"):
            query["api_key"] = self._api_key
        session = await self._get_session()
        try:
            async with session.get(f"{BASE_URL}{path}", params=query) as resp:
                payload = await resp.json(content_type=None)
                if resp.status == 401:
                    raise TMDBError("Clé TMDB refusée (401). Vérifie TMDB_API_KEY.")
                if resp.status == 404:
                    raise TMDBError("Film introuvable sur TMDB.")
                if resp.status >= 400:
                    message = (payload or {}).get("status_message", f"erreur HTTP {resp.status}")
                    raise TMDBError(f"TMDB : {message}")
                return payload or {}
        except aiohttp.ClientError as exc:
            raise TMDBError(f"TMDB inaccessible : {exc}") from exc

    async def search(self, query: str, limit: int = 25) -> list[Movie]:
        query = query.strip()
        if not query:
            return []
        payload = await self._request("/search/movie", query=query, include_adult="false")
        results = payload.get("results") or []
        return [Movie.from_api(item) for item in results[:limit]]

    async def movie(self, movie_id: int) -> Movie:
        return Movie.from_api(await self._request(f"/movie/{movie_id}"))

    async def resolve(self, reference: str) -> Movie:
        """Résout un nom, un id TMDB ou une valeur d'autocomplétion 'tmdb:<id>'."""
        reference = reference.strip()
        if reference.startswith("tmdb:"):
            reference = reference[len("tmdb:") :]
        if reference.isdigit():
            return await self.movie(int(reference))
        results = await self.search(reference, limit=1)
        if not results:
            raise TMDBError(f"Aucun film trouvé pour « {reference} ».")
        return await self.movie(results[0].id)
