"""Séances de cinéma : création par les admins, inscription par réaction."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    DATA_DIR,
    EMOJI_INSCRIPTION,
    LIEU_PAR_DEFAUT,
    LIEUX,
    TIMEZONE,
)
from storage import JsonStore
from tmdb import Movie, TMDBClient, TMDBError

log = logging.getLogger("coincoin.cinema")

COULEUR = discord.Colour.from_str("#e0668a")
DATE_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")
HEURE_RE = re.compile(r"^(\d{1,2})\s*[:hH]\s*(\d{2})?$")
MENTION_RE = re.compile(r"^<@!?(\d+)>$")
MAX_SYNOPSIS = 1000


def parse_date(raw: str) -> tuple[int, int, int]:
    match = DATE_RE.match(raw.strip())
    if not match:
        raise ValueError("Format de date invalide. Utilise `JJ/MM/AAAA` (par exemple `14/02/2026`).")
    jour, mois, annee = int(match.group(1)), int(match.group(2)), match.group(3)
    if annee is None:
        an = datetime.now(TIMEZONE).year
    else:
        an = int(annee)
        if an < 100:
            an += 2000
    return an, mois, jour


def parse_heure(raw: str) -> tuple[int, int]:
    match = HEURE_RE.match(raw.strip())
    if not match:
        raise ValueError("Format d'heure invalide. Utilise `HH:MM` (par exemple `20:30`).")
    return int(match.group(1)), int(match.group(2) or 0)


def parse_datetime(date: str, heure: str) -> datetime:
    annee, mois, jour = parse_date(date)
    h, m = parse_heure(heure)
    try:
        return datetime(annee, mois, jour, h, m, tzinfo=TIMEZONE)
    except ValueError as exc:
        raise ValueError(f"Date ou heure impossible : {exc}") from exc


def _noms(membre: discord.Member) -> tuple[str, ...]:
    return tuple(
        nom.casefold()
        for nom in (membre.name, membre.display_name, membre.global_name)
        if nom
    )


def _correspond(membre: discord.Member, terme: str) -> bool:
    return any(terme in nom for nom in _noms(membre))


class Cinema(commands.GroupCog, name="cinema", description="Séances de cinéma du serveur"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = JsonStore(DATA_DIR / "screenings.json", default={})
        self.tmdb = TMDBClient(bot.settings.tmdb_api_key, bot.settings.tmdb_language)

    async def cog_unload(self) -> None:
        await self.tmdb.close()

    # ------------------------------------------------------------------ embed

    def build_embed(self, record: dict[str, Any], guild: discord.Guild | None) -> discord.Embed:
        movie = Movie.from_dict(record["movie"])
        lieu = LIEUX.get(record["lieu"], LIEUX[LIEU_PAR_DEFAUT])
        debut = datetime.fromisoformat(record["datetime"])
        participants: list[int] = record["participants"]
        places: int = record["places"]
        restantes = max(places - len(participants), 0)

        synopsis = movie.overview or "*Synopsis indisponible.*"
        if len(synopsis) > MAX_SYNOPSIS:
            synopsis = synopsis[: MAX_SYNOPSIS - 1].rstrip() + "…"

        embed = discord.Embed(
            title=f"🎬 {movie.label}",
            url=movie.url,
            description=synopsis,
            colour=COULEUR if not record.get("annulee") else discord.Colour.dark_grey(),
        )
        if record.get("annulee"):
            embed.title = f"❌ ANNULÉE — {movie.label}"

        embed.add_field(name="📍 Cinéma", value=f"**{lieu}**", inline=True)
        embed.add_field(
            name="🗓️ Séance",
            value=f"{discord.utils.format_dt(debut, 'F')}\n({discord.utils.format_dt(debut, 'R')})",
            inline=True,
        )
        details = []
        if movie.runtime:
            details.append(f"⏱️ {movie.runtime // 60} h {movie.runtime % 60:02d}")
        if movie.genres:
            details.append("🏷️ " + ", ".join(movie.genres[:3]))
        if details:
            embed.add_field(name="Infos", value="\n".join(details), inline=True)

        embed.add_field(
            name="🎫 Places",
            value=f"**{restantes}** restante(s) sur **{places}**"
            + ("\n*Complet !*" if restantes == 0 else ""),
            inline=False,
        )

        if participants:
            mentions = [f"<@{uid}>" for uid in participants]
            valeur = ", ".join(mentions[:30])
            if len(mentions) > 30:
                valeur += f" … (+{len(mentions) - 30})"
        else:
            valeur = "*Personne pour l'instant.*"
        embed.add_field(name=f"👥 Inscrits ({len(participants)})", value=valeur, inline=False)

        if movie.poster_url:
            embed.set_image(url=movie.poster_url)

        if record.get("annulee"):
            embed.set_footer(text="Séance annulée.")
        else:
            embed.set_footer(
                text=f"Réagis avec {EMOJI_INSCRIPTION} pour t'inscrire · retire ta réaction pour te désinscrire"
            )
        if guild is not None and (organisateur := guild.get_member(record["auteur"])):
            embed.set_author(name=f"Proposé par {organisateur.display_name}", icon_url=organisateur.display_avatar.url)
        return embed

    async def _refresh(self, record: dict[str, Any]) -> None:
        """Réédite le message d'une séance pour refléter l'état du stockage."""
        channel = self.bot.get_channel(record["channel_id"])
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(record["message_id"])
            guild = getattr(channel, "guild", None)
            await message.edit(embed=self.build_embed(record, guild))
        except discord.HTTPException as exc:
            log.warning("Mise à jour de la séance %s impossible : %s", record["message_id"], exc)

    def _record(self, message_id: int) -> dict[str, Any] | None:
        return self.store.data().get(str(message_id))

    async def _fetch_screening(
        self, interaction: discord.Interaction, message: str
    ) -> dict[str, Any]:
        reference = message.strip().rsplit("/", 1)[-1]
        if not reference.isdigit():
            raise ValueError("Donne l'identifiant (ou le lien) du message de la séance.")
        record = self._record(int(reference))
        if record is None or record.get("guild_id") != interaction.guild_id:
            raise ValueError(f"Aucune séance connue pour le message `{reference}`.")
        return record

    async def _search_members(self, guild: discord.Guild, terme: str) -> list[discord.Member]:
        """Membres du cache, complétés par une requête à Discord si besoin."""
        membres = [membre for membre in guild.members if not membre.bot]
        if terme and not any(_correspond(membre, terme) for membre in membres):
            try:
                trouves = await guild.query_members(query=terme, limit=25)
            except (discord.HTTPException, asyncio.TimeoutError):
                trouves = []
            membres += [membre for membre in trouves if not membre.bot and membre not in membres]
        return membres

    async def _resolve_member(self, guild: discord.Guild, brut: str) -> discord.Member:
        """Retrouve un membre à partir d'un identifiant, d'une mention ou d'un pseudo."""
        brut = brut.strip()
        if not brut:
            raise ValueError("Indique un pseudo ou un identifiant Discord.")

        mention = MENTION_RE.match(brut)
        identifiant = mention.group(1) if mention else (brut if brut.isdigit() else None)
        if identifiant is not None:
            membre = guild.get_member(int(identifiant))
            if membre is None:
                try:
                    membre = await guild.fetch_member(int(identifiant))
                except discord.NotFound:
                    raise ValueError(f"Aucun membre `{identifiant}` sur ce serveur.") from None
                except discord.HTTPException:
                    raise ValueError(f"Impossible de récupérer le membre `{identifiant}`.") from None
            return membre

        terme = brut.lstrip("@").casefold()
        membres = await self._search_members(guild, terme)
        exacts = [membre for membre in membres if terme in _noms(membre)]
        candidats = exacts or [
            membre for membre in membres if any(terme in nom for nom in _noms(membre))
        ]

        if not candidats:
            raise ValueError(
                f"Aucun membre ne correspond à `{brut}`. Essaie avec son identifiant Discord."
            )
        if len(candidats) > 1:
            apercu = ", ".join(f"{membre.display_name} (`{membre.id}`)" for membre in candidats[:5])
            reste = "…" if len(candidats) > 5 else ""
            raise ValueError(
                f"Plusieurs membres correspondent à `{brut}` : {apercu}{reste}\n"
                "Précise l'identifiant Discord."
            )
        return candidats[0]

    # --------------------------------------------------------- autocomplétion

    async def film_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if len(current.strip()) < 2 or not self.tmdb.configured:
            return []
        try:
            films = await self.tmdb.search(current, limit=25)
        except TMDBError:
            return []
        return [
            app_commands.Choice(name=film.label[:100], value=f"tmdb:{film.id}") for film in films
        ]

    async def membre_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        terme = current.strip().lstrip("@").casefold()
        membres = await self._search_members(guild, terme)
        if terme:
            membres = [
                membre
                for membre in membres
                if _correspond(membre, terme) or terme in str(membre.id)
            ]
        membres.sort(key=lambda membre: membre.display_name.casefold())
        return [
            app_commands.Choice(name=f"{membre.display_name} (@{membre.name})"[:100], value=str(membre.id))
            for membre in membres[:25]
        ]

    # --------------------------------------------------------------- commandes

    @app_commands.command(name="seance", description="Créer une séance de cinéma")
    @app_commands.describe(
        film="Nom du film (autocomplétion TMDB) ou identifiant TMDB",
        date="Date de la séance, format JJ/MM/AAAA",
        heure="Heure de la séance, format HH:MM",
        places="Nombre de places disponibles",
        lieu="Cinéma (Le Palais des Cerises par défaut)",
        salon="Salon de publication (par défaut : le salon cinéma configuré)",
    )
    @app_commands.choices(
        lieu=[app_commands.Choice(name=nom, value=cle) for cle, nom in LIEUX.items()]
    )
    @app_commands.autocomplete(film=film_autocomplete)
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.default_permissions(manage_events=True)
    @app_commands.guild_only()
    async def seance(
        self,
        interaction: discord.Interaction,
        film: str,
        date: str,
        heure: str,
        places: app_commands.Range[int, 1, 500],
        lieu: app_commands.Choice[str] | None = None,
        salon: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            debut = parse_datetime(date, heure)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        try:
            movie = await self.tmdb.resolve(film)
        except TMDBError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        channel = salon
        if channel is None:
            channel_id = self.bot.settings.cinema_channel_id
            channel = self.bot.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.followup.send(
                "Salon cinéma introuvable : vérifie `CINEMA_CHANNEL_ID` dans le .env "
                "ou précise l'option `salon`.",
                ephemeral=True,
            )
            return

        record: dict[str, Any] = {
            "guild_id": interaction.guild_id,
            "channel_id": channel.id,
            "message_id": 0,
            "movie": movie.to_dict(),
            "lieu": (lieu.value if lieu else LIEU_PAR_DEFAUT),
            "datetime": debut.isoformat(),
            "places": int(places),
            "participants": [],
            "auteur": interaction.user.id,
            "annulee": False,
        }

        try:
            message = await channel.send(embed=self.build_embed(record, interaction.guild))
            await message.add_reaction(EMOJI_INSCRIPTION)
        except discord.Forbidden:
            await interaction.followup.send(
                f"Je n'ai pas la permission de publier (ou de réagir) dans {channel.mention}.",
                ephemeral=True,
            )
            return

        record["message_id"] = message.id
        async with self.store.lock:
            self.store.data()[str(message.id)] = record
            self.store.save()

        await interaction.followup.send(
            f"Séance créée : {message.jump_url}\n**{movie.label}** — "
            f"{LIEUX[record['lieu']]}, {debut:%d/%m/%Y à %H:%M}, {places} place(s).",
            ephemeral=True,
        )

    @app_commands.command(name="liste", description="Lister les séances à venir")
    @app_commands.guild_only()
    async def liste(self, interaction: discord.Interaction) -> None:
        maintenant = datetime.now(TIMEZONE)
        lignes = []
        for record in self.store.data().values():
            if record.get("guild_id") != interaction.guild_id or record.get("annulee"):
                continue
            debut = datetime.fromisoformat(record["datetime"])
            if debut < maintenant:
                continue
            movie = Movie.from_dict(record["movie"])
            lien = (
                f"https://discord.com/channels/{record['guild_id']}"
                f"/{record['channel_id']}/{record['message_id']}"
            )
            restantes = max(record["places"] - len(record["participants"]), 0)
            lignes.append(
                (debut, f"{discord.utils.format_dt(debut, 'f')} — [**{movie.label}**]({lien})\n"
                 f"　{LIEUX.get(record['lieu'], LIEUX[LIEU_PAR_DEFAUT])} · {restantes} place(s) restante(s)")
            )
        lignes.sort(key=lambda item: item[0])

        embed = discord.Embed(
            title="🍿 Séances à venir",
            description="\n\n".join(texte for _, texte in lignes) if lignes else "Aucune séance programmée.",
            colour=COULEUR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="participants", description="Voir les inscrits d'une séance")
    @app_commands.describe(message="Identifiant ou lien du message de la séance")
    @app_commands.guild_only()
    async def participants(self, interaction: discord.Interaction, message: str) -> None:
        try:
            record = await self._fetch_screening(interaction, message)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        movie = Movie.from_dict(record["movie"])
        inscrits = record["participants"]
        corps = (
            "\n".join(f"{i}. <@{uid}>" for i, uid in enumerate(inscrits, start=1))
            if inscrits
            else "*Personne pour l'instant.*"
        )
        embed = discord.Embed(
            title=f"Inscrits — {movie.label}",
            description=corps,
            colour=COULEUR,
        )
        embed.set_footer(text=f"{len(inscrits)}/{record['places']} place(s) prise(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="inscrire", description="Inscrire de force un membre à une séance")
    @app_commands.describe(
        message="Identifiant ou lien du message de la séance",
        membre="Pseudo, mention ou identifiant Discord",
    )
    @app_commands.autocomplete(membre=membre_autocomplete)
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.default_permissions(manage_events=True)
    @app_commands.guild_only()
    async def inscrire(self, interaction: discord.Interaction, message: str, membre: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            cible = await self._resolve_member(interaction.guild, membre)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        if cible.bot:
            await interaction.followup.send("Les bots ne s'inscrivent pas aux séances.", ephemeral=True)
            return

        async with self.store.lock:
            try:
                record = await self._fetch_screening(interaction, message)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            if record.get("annulee"):
                await interaction.followup.send(
                    "Cette séance est annulée : impossible d'y inscrire quelqu'un.", ephemeral=True
                )
                return
            if cible.id in record["participants"]:
                await interaction.followup.send(
                    f"{cible.mention} est déjà inscrit à cette séance.", ephemeral=True
                )
                return
            record["participants"].append(cible.id)
            self.store.save()
            surplus = len(record["participants"]) - record["places"]
            await self._refresh(record)

        movie = Movie.from_dict(record["movie"])
        lien = (
            f"https://discord.com/channels/{record['guild_id']}"
            f"/{record['channel_id']}/{record['message_id']}"
        )
        # Le bot ne peut pas réagir à la place du membre : sans réaction sur le
        # message, la désinscription passe forcément par /cinema desinscrire.
        try:
            await cible.send(
                f"🎟️ {interaction.user.display_name} t'a inscrit·e à la séance "
                f"**{movie.label}** : {lien}"
            )
            avis = ""
        except discord.HTTPException:
            avis = "\n(MP impossible : ses messages privés sont fermés.)"

        avertissement = (
            f"\n⚠️ La séance compte maintenant {len(record['participants'])} inscrit(s) "
            f"pour {record['places']} place(s)."
            if surplus > 0
            else ""
        )
        await interaction.followup.send(
            f"{cible.mention} est inscrit·e à **{movie.label}**.{avertissement}{avis}",
            ephemeral=True,
        )

    @app_commands.command(name="desinscrire", description="Retirer un membre d'une séance")
    @app_commands.describe(
        message="Identifiant ou lien du message de la séance",
        membre="Pseudo, mention ou identifiant Discord",
    )
    @app_commands.autocomplete(membre=membre_autocomplete)
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.default_permissions(manage_events=True)
    @app_commands.guild_only()
    async def desinscrire(self, interaction: discord.Interaction, message: str, membre: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            cible = await self._resolve_member(interaction.guild, membre)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        async with self.store.lock:
            try:
                record = await self._fetch_screening(interaction, message)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            if cible.id not in record["participants"]:
                await interaction.followup.send(
                    f"{cible.mention} n'est pas inscrit·e à cette séance.", ephemeral=True
                )
                return
            record["participants"].remove(cible.id)
            self.store.save()
            await self._refresh(record)

        # La réaction éventuelle doit partir aussi, sinon elle reste affichée
        # sans inscription correspondante (et un re-clic ne changerait rien).
        await self._remove_reaction(record, cible.id)

        movie = Movie.from_dict(record["movie"])
        await interaction.followup.send(
            f"{cible.mention} a été retiré·e de **{movie.label}**.", ephemeral=True
        )

    @app_commands.command(name="places", description="Modifier le nombre de places d'une séance")
    @app_commands.describe(
        message="Identifiant ou lien du message de la séance",
        nombre="Nouveau nombre total de places",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.default_permissions(manage_events=True)
    @app_commands.guild_only()
    async def places(
        self,
        interaction: discord.Interaction,
        message: str,
        nombre: app_commands.Range[int, 1, 500],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self.store.lock:
            try:
                record = await self._fetch_screening(interaction, message)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            record["places"] = int(nombre)
            self.store.save()
            surplus = len(record["participants"]) - int(nombre)
            await self._refresh(record)

        avertissement = (
            f"\n⚠️ Il y a {surplus} inscrit(s) de trop : personne n'a été retiré, "
            "désinscris-les manuellement si besoin."
            if surplus > 0
            else ""
        )
        await interaction.followup.send(f"Séance mise à jour : {nombre} place(s).{avertissement}", ephemeral=True)

    @app_commands.command(name="annuler", description="Annuler une séance")
    @app_commands.describe(
        message="Identifiant ou lien du message de la séance",
        raison="Raison affichée aux inscrits (optionnel)",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.default_permissions(manage_events=True)
    @app_commands.guild_only()
    async def annuler(
        self, interaction: discord.Interaction, message: str, raison: str = ""
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        async with self.store.lock:
            try:
                record = await self._fetch_screening(interaction, message)
            except ValueError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            record["annulee"] = True
            inscrits = list(record["participants"])
            self.store.save()
            await self._refresh(record)

        movie = Movie.from_dict(record["movie"])
        suffixe = f"\nRaison : {raison}" if raison else ""
        prevenus = 0
        for user_id in inscrits:
            user = self.bot.get_user(user_id) or await self._safe_fetch_user(user_id)
            if user is None:
                continue
            try:
                await user.send(f"❌ La séance **{movie.label}** a été annulée.{suffixe}")
                prevenus += 1
            except discord.HTTPException:
                pass

        await interaction.followup.send(
            f"Séance annulée. {prevenus}/{len(inscrits)} inscrit(s) prévenu(s) en MP.", ephemeral=True
        )

    async def _safe_fetch_user(self, user_id: int) -> discord.User | None:
        try:
            return await self.bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    # --------------------------------------------------------------- événements

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != EMOJI_INSCRIPTION:
            return
        if payload.member is not None and payload.member.bot:
            return

        inscrit = False
        async with self.store.lock:
            record = self._record(payload.message_id)
            if record is None:
                return

            if record.get("annulee"):
                motif = "cette séance a été annulée"
            elif payload.user_id in record["participants"]:
                return
            elif len(record["participants"]) >= record["places"]:
                motif = "il n'y a plus de place disponible"
            else:
                record["participants"].append(payload.user_id)
                self.store.save()
                inscrit = True

        if inscrit:
            await self._refresh(record)
            return

        # Inscription refusée : on retire la réaction et on prévient la personne.
        movie = Movie.from_dict(record["movie"])
        await self._remove_reaction(record, payload.user_id)
        user = self.bot.get_user(payload.user_id) or await self._safe_fetch_user(payload.user_id)
        if user is not None:
            try:
                await user.send(
                    f"Je n'ai pas pu t'inscrire à la séance **{movie.label}** : {motif}."
                )
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        if str(payload.emoji) != EMOJI_INSCRIPTION:
            return

        async with self.store.lock:
            record = self._record(payload.message_id)
            if record is None or payload.user_id not in record["participants"]:
                return
            record["participants"].remove(payload.user_id)
            self.store.save()
        await self._refresh(record)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        async with self.store.lock:
            if self.store.data().pop(str(payload.message_id), None) is not None:
                self.store.save()

    async def _remove_reaction(self, record: dict[str, Any], user_id: int) -> None:
        channel = self.bot.get_channel(record["channel_id"])
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(record["message_id"])
            await message.remove_reaction(EMOJI_INSCRIPTION, discord.Object(id=user_id))
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Cinema(bot))
