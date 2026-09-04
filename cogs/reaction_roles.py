"""Rôles-réaction : un emoji sur un message = un rôle attribué / retiré."""

from __future__ import annotations

import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from config import DATA_DIR
from storage import JsonStore

log = logging.getLogger("coincoin.roles")

MESSAGE_LINK_RE = re.compile(r"/channels/(?:\d+|@me)/(\d+)/(\d+)")


def parse_emoji(raw: str) -> discord.PartialEmoji | None:
    """Normalise un emoji saisi par un humain (unicode ou <:nom:id>)."""
    raw = raw.strip()
    if not raw:
        return None
    emoji = discord.PartialEmoji.from_str(raw)
    # from_str accepte n'importe quel texte comme emoji "unicode" : on écarte
    # tout ce qui est purement ASCII, ce qu'aucun emoji unicode n'est.
    if emoji.id is None and (not emoji.name or emoji.name.isascii()):
        return None
    return emoji


class ReactionRoles(commands.GroupCog, name="rolereaction", description="Gérer les rôles-réaction"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = JsonStore(DATA_DIR / "reaction_roles.json", default={})

    # ------------------------------------------------------------------ outils

    async def _resolve_message(
        self,
        interaction: discord.Interaction,
        reference: str,
        salon: discord.TextChannel | None,
    ) -> discord.Message:
        """Accepte un lien de message ou un identifiant brut (+ salon optionnel)."""
        reference = reference.strip()
        match = MESSAGE_LINK_RE.search(reference)
        if match:
            channel_id, message_id = int(match.group(1)), int(match.group(2))
            channel = salon or self.bot.get_channel(channel_id)
        elif reference.isdigit():
            message_id = int(reference)
            channel = salon or interaction.channel
        else:
            raise ValueError("Donne un identifiant de message ou un lien de message Discord.")

        if not isinstance(channel, discord.abc.Messageable):
            raise ValueError("Salon introuvable. Précise l'option `salon`.")
        try:
            return await channel.fetch_message(message_id)
        except discord.NotFound:
            raise ValueError(
                f"Message `{message_id}` introuvable dans {getattr(channel, 'mention', 'ce salon')}. "
                "Précise l'option `salon` si le message est ailleurs."
            ) from None
        except discord.Forbidden:
            raise ValueError("Je n'ai pas la permission de lire ce salon.") from None

    def _record(self, message_id: int) -> dict | None:
        return self.store.data().get(str(message_id))

    async def _refresh_panel(self, message: discord.Message, record: dict) -> None:
        """Met à jour la légende du panneau si le message vient du bot."""
        panel = record.get("panel")
        if not panel or message.author.id != self.bot.user.id:
            return
        lignes = []
        for emoji, role_id in record["roles"].items():
            role = message.guild.get_role(role_id) if message.guild else None
            lignes.append(f"{emoji} → {role.mention if role else f'`rôle {role_id} supprimé`'}")
        embed = discord.Embed(
            title=panel.get("titre") or "Rôles-réaction",
            description="\n".join(filter(None, [panel.get("texte"), "", *lignes])),
            colour=discord.Colour.from_str("#f4a7c0"),
        )
        embed.set_footer(text="Réagis pour obtenir un rôle, retire ta réaction pour l'enlever.")
        await message.edit(embed=embed)

    # --------------------------------------------------------------- commandes

    @app_commands.command(name="panneau", description="Créer un message de rôles-réaction vide")
    @app_commands.describe(
        titre="Titre de l'embed",
        texte="Texte d'introduction affiché au-dessus de la liste des rôles",
        salon="Salon de publication (par défaut : le salon courant)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def panneau(
        self,
        interaction: discord.Interaction,
        titre: str,
        texte: str = "",
        salon: discord.TextChannel | None = None,
    ) -> None:
        channel = salon or interaction.channel
        if not isinstance(channel, discord.abc.Messageable):
            await interaction.response.send_message("Salon invalide.", ephemeral=True)
            return

        embed = discord.Embed(
            title=titre,
            description=texte or "Aucun rôle configuré pour le moment.",
            colour=discord.Colour.from_str("#f4a7c0"),
        )
        embed.set_footer(text="Réagis pour obtenir un rôle, retire ta réaction pour l'enlever.")
        message = await channel.send(embed=embed)

        async with self.store.lock:
            self.store.data()[str(message.id)] = {
                "guild_id": interaction.guild_id,
                "channel_id": channel.id,
                "panel": {"titre": titre, "texte": texte},
                "roles": {},
            }
            self.store.save()

        await interaction.response.send_message(
            f"Panneau créé : {message.jump_url}\nAjoute des rôles avec "
            f"`/rolereaction ajouter message:{message.id} emoji:… role:…`",
            ephemeral=True,
        )

    @app_commands.command(name="ajouter", description="Associer un emoji à un rôle sur un message")
    @app_commands.describe(
        message="Identifiant ou lien du message",
        emoji="Emoji déclencheur",
        role="Rôle à attribuer",
        salon="Salon du message (si ce n'est pas le salon courant)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def ajouter(
        self,
        interaction: discord.Interaction,
        message: str,
        emoji: str,
        role: discord.Role,
        salon: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        parsed = parse_emoji(emoji)
        if parsed is None:
            await interaction.followup.send(f"`{emoji}` n'est pas un emoji valide.", ephemeral=True)
            return
        if role.managed or role.is_default():
            await interaction.followup.send(
                "Ce rôle est géré par une intégration (ou c'est @everyone) : il ne peut pas être attribué.",
                ephemeral=True,
            )
            return
        me = interaction.guild.me
        if role >= me.top_role:
            await interaction.followup.send(
                f"{role.mention} est au-dessus de mon rôle le plus haut : je ne peux pas l'attribuer. "
                "Remonte mon rôle dans les paramètres du serveur.",
                ephemeral=True,
            )
            return
        if not me.guild_permissions.manage_roles:
            await interaction.followup.send("Il me manque la permission « Gérer les rôles ».", ephemeral=True)
            return

        try:
            target = await self._resolve_message(interaction, message, salon)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        try:
            await target.add_reaction(parsed)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Impossible d'ajouter la réaction {emoji} : {exc.text or exc}. "
                "L'emoji doit être accessible au bot.",
                ephemeral=True,
            )
            return

        async with self.store.lock:
            data = self.store.data()
            record = data.setdefault(
                str(target.id),
                {
                    "guild_id": interaction.guild_id,
                    "channel_id": target.channel.id,
                    "panel": None,
                    "roles": {},
                },
            )
            record["roles"][str(parsed)] = role.id
            self.store.save()
            await self._refresh_panel(target, record)

        await interaction.followup.send(
            f"{parsed} donnera désormais {role.mention} sur {target.jump_url}", ephemeral=True
        )

    @app_commands.command(name="retirer", description="Supprimer une association emoji ↔ rôle")
    @app_commands.describe(
        message="Identifiant ou lien du message",
        emoji="Emoji à dissocier",
        salon="Salon du message (si ce n'est pas le salon courant)",
    )
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def retirer(
        self,
        interaction: discord.Interaction,
        message: str,
        emoji: str,
        salon: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        parsed = parse_emoji(emoji)
        if parsed is None:
            await interaction.followup.send(f"`{emoji}` n'est pas un emoji valide.", ephemeral=True)
            return

        try:
            target = await self._resolve_message(interaction, message, salon)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        async with self.store.lock:
            data = self.store.data()
            record = data.get(str(target.id))
            if not record or str(parsed) not in record["roles"]:
                await interaction.followup.send(
                    f"Aucun rôle n'est associé à {parsed} sur ce message.", ephemeral=True
                )
                return
            record["roles"].pop(str(parsed))
            if not record["roles"] and not record.get("panel"):
                data.pop(str(target.id))
                record = None
            self.store.save()
            if record:
                await self._refresh_panel(target, record)

        try:
            await target.clear_reaction(parsed)
        except discord.HTTPException:
            pass

        await interaction.followup.send(f"Association {parsed} supprimée.", ephemeral=True)

    @app_commands.command(name="lister", description="Lister les rôles-réaction configurés")
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.guild_only()
    async def lister(self, interaction: discord.Interaction) -> None:
        data = self.store.data()
        blocs: list[str] = []
        for message_id, record in data.items():
            if record.get("guild_id") != interaction.guild_id:
                continue
            lien = f"https://discord.com/channels/{interaction.guild_id}/{record['channel_id']}/{message_id}"
            lignes = [
                f"  {emoji} → <@&{role_id}>" for emoji, role_id in record["roles"].items()
            ] or ["  *(aucun rôle)*"]
            blocs.append(f"[Message {message_id}]({lien})\n" + "\n".join(lignes))

        embed = discord.Embed(
            title="Rôles-réaction",
            description="\n\n".join(blocs) if blocs else "Aucun rôle-réaction configuré.",
            colour=discord.Colour.from_str("#f4a7c0"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --------------------------------------------------------------- événements

    async def _apply(self, payload: discord.RawReactionActionEvent, ajouter: bool) -> None:
        if payload.guild_id is None or payload.user_id == self.bot.user.id:
            return
        record = self._record(payload.message_id)
        if not record:
            return
        role_id = record["roles"].get(str(payload.emoji))
        if role_id is None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        role = guild.get_role(role_id)
        if role is None:
            log.warning("Rôle %s introuvable (message %s)", role_id, payload.message_id)
            return

        member = payload.member if ajouter else None
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return

        try:
            if ajouter:
                await member.add_roles(role, reason="Rôle-réaction")
            else:
                await member.remove_roles(role, reason="Rôle-réaction")
        except discord.Forbidden:
            log.warning("Permissions insuffisantes pour modifier %s sur %s", role, member)
        except discord.HTTPException as exc:
            log.warning("Échec de la modification de rôle : %s", exc)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._apply(payload, ajouter=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._apply(payload, ajouter=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
