"""Coin coin — bot Discord du serveur Canard Gay."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import ConfigError, Settings

EXTENSIONS = ("cogs.reaction_roles", "cogs.cinema")

log = logging.getLogger("coincoin")


class CoinCoin(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        intents = discord.Intents.default()
        intents.members = True  # nécessaire pour attribuer les rôles-réaction
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False),
        )

    async def setup_hook(self) -> None:
        self.tree.on_error = self.on_app_command_error

        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("Extension chargée : %s", extension)

        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("%d commandes synchronisées sur le serveur %s", len(synced), self.settings.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("%d commandes synchronisées globalement", len(synced))

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            message = "Tu n'as pas les permissions nécessaires pour cette commande."
        elif isinstance(error, app_commands.BotMissingPermissions):
            manquantes = ", ".join(error.missing_permissions)
            message = f"Il me manque des permissions : {manquantes}."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Doucement : réessaie dans {error.retry_after:.0f} s."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "Cette commande ne fonctionne que sur un serveur."
        else:
            log.exception("Erreur dans la commande %s", getattr(interaction.command, "name", "?"), exc_info=error)
            message = "Oups, une erreur inattendue est survenue. Les détails sont dans les logs."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_ready(self) -> None:
        log.info("Connecté en tant que %s (%s)", self.user, getattr(self.user, "id", "?"))
        await self.change_presence(activity=discord.Game(name="coin coin 🦆"))


async def main() -> None:
    discord.utils.setup_logging(level=logging.INFO)
    try:
        settings = Settings.load()
    except ConfigError as exc:
        raise SystemExit(f"Configuration invalide : {exc}")

    if not settings.tmdb_api_key:
        log.warning("TMDB_API_KEY absente : les commandes /cinema de création échoueront.")

    async with CoinCoin(settings) as bot:
        await bot.start(settings.token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
