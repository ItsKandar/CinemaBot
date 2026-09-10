# Coin coin
Bot discord pour mon serveur :)

# Objectifs

## Rôles-réaction

Permettre aux membres de s'attribuer eux-mêmes des rôles en réagissant à un message,
et de les retirer en enlevant leur réaction.

## Séances de cinéma

Permettre aux admins d'organiser des sorties ciné auxquelles les membres s'inscrivent
par une simple réaction.

- **Création par un admin** : le bot recherche le film sur TMDB (par titre ou par identifiant),
  puis publie l'annonce dans le salon cinéma (`1545391737806262322`).
- **Cinéma** au choix parmi *Le Palais des Cerises* (par défaut), *Opéraims* et *Pathé Thillois*.
- **Annonce** sous forme d'embed contenant le titre du film, son synopsis, son affiche,
  le cinéma, la date et l'heure de la séance, et le nombre de places restantes
  (le total étant fixé par l'admin).
- **Inscriptions** gérées par le bot via les réactions : réagir inscrit, retirer sa réaction
  désinscrit, et le compteur de places se met à jour à chaque fois.

# Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# créer un fichier .env avec les variables ci-dessous
.venv/bin/python bot.py
```

## Variables d'environnement (`.env`)

| Variable | Obligatoire | Rôle |
| --- | --- | --- |
| `DISCORD_TOKEN` | oui | Token du bot ([portail développeur](https://discord.com/developers/applications)) |
| `TMDB_API_KEY` | oui pour le cinéma | Clé API v3 **ou** token v4 TMDB ([réglages TMDB](https://www.themoviedb.org/settings/api)) |
| `CINEMA_CHANNEL_ID` | non | Salon des séances (`1545391737806262322`) |
| `GUILD_ID` | non | ID du serveur : synchro instantanée des commandes slash (sinon global, jusqu'à 1 h) |
| `TMDB_LANGUAGE` | non | Langue des données TMDB (`fr-FR` par défaut) |

## Configuration côté Discord

- Portail développeur → Bot → activer **Server Members Intent** (nécessaire aux rôles-réaction).
- Inviter le bot avec le scope `bot applications.commands` et les permissions :
  gérer les rôles, voir les salons, envoyer des messages, intégrer des liens,
  ajouter des réactions, gérer les messages, lire l'historique.
- Le rôle du bot doit être **au-dessus** des rôles qu'il distribue.

# Docker

```sh
# créer un fichier .env (cf. les variables d'environnement plus haut)
docker compose up -d --build
docker compose logs -f
```

- Le `.env` n'est **pas** copié dans l'image : les variables sont injectées au démarrage
  via `env_file`. Après modification du `.env`, un `docker compose up -d` suffit.
- L'état (inscriptions, rôles-réaction) est conservé dans le volume nommé `coincoin-data`,
  il survit aux `--build` et aux redémarrages.
- Le conteneur tourne sans privilèges (uid 10001) et redémarre automatiquement,
  sauf arrêt explicite. Fuseau `Europe/Paris`.

## Consulter les données du conteneur

Les JSON vivent dans le volume, pas dans `./data`. Trois façons d'y accéder :

```sh
# 1. depuis le conteneur en marche
docker compose exec coincoin cat data/screenings.json

# 2. en copiant le dossier sur l'hôte (marche même conteneur arrêté)
docker cp coincoin:/app/data ./data-copie

# 3. directement sur le disque, sans passer par docker (podman rootless)
ls "$(podman volume inspect canardgaydiscord_coincoin-data --format '{{.Mountpoint}}')"
```

### Ou : avoir les fichiers directement dans `./data`

Si tu préfères éditer les JSON dans ton éditeur, remplace dans [compose.yaml](compose.yaml)
la ligne du volume par un bind mount et force l'utilisateur du conteneur :

```yaml
    user: "0:0"          # podman rootless : uid 0 dans le conteneur = ton compte sur l'hôte
    volumes:
      - ./data:/app/data
```

Sans le `user:`, le conteneur (uid 10001) n'a pas le droit d'écrire dans un dossier
qui t'appartient et le bot plante à la première inscription (`PermissionError`).
Avec **Docker** (pas podman) il faut mettre ton propre uid à la place : `user: "1000:1000"`.

Sans compose :

```sh
docker build -t coincoin-bot .
docker run -d --name coincoin --restart unless-stopped \
  --env-file .env -v coincoin-data:/app/data coincoin-bot
```

## Podman

Les mêmes fichiers fonctionnent avec podman en rootless, sans aucun privilège root.
`podman compose` délègue à un provider externe qui a besoin du socket utilisateur ;
il suffit de l'activer une fois :

```sh
systemctl --user enable --now podman.socket
podman compose up -d
podman logs -f coincoin
```

Ou directement, sans compose ni socket :

```sh
podman build -t coincoin-bot .
podman run -d --name coincoin --restart unless-stopped \
  --env-file .env -v coincoin-data:/app/data coincoin-bot
```

Les avertissements `PyNaCl is not installed, voice will NOT be supported` au démarrage
sont normaux : le bot n'utilise pas le vocal.

# Commandes

## Rôles-réaction (permission « Gérer les rôles »)

| Commande | Effet |
| --- | --- |
| `/rolereaction panneau titre texte [salon]` | Publie un message de rôles-réaction, dont la légende se met à jour automatiquement |
| `/rolereaction ajouter message emoji role [salon]` | Associe un emoji à un rôle sur n'importe quel message (ID ou lien) |
| `/rolereaction retirer message emoji [salon]` | Supprime une association |
| `/rolereaction lister` | Liste la configuration du serveur |

Réagir attribue le rôle, retirer la réaction le retire.

## Séances de cinéma (permission « Gérer les événements »)

| Commande | Effet |
| --- | --- |
| `/cinema seance film date heure places [lieu] [salon]` | Crée la séance et publie l'embed dans le salon cinéma |
| `/cinema liste` | Séances à venir |
| `/cinema participants message` | Liste des inscrits d'une séance |
| `/cinema inscrire message membre` | Inscrit de force un membre (pseudo, mention ou ID) |
| `/cinema desinscrire message membre` | Retire un membre de la séance |
| `/cinema places message nombre` | Change le nombre total de places |
| `/cinema annuler message [raison]` | Annule la séance et prévient les inscrits en MP |

- `film` : autocomplétion TMDB en direct ; un identifiant TMDB ou un titre libre marchent aussi.
- `date` : `JJ/MM/AAAA` (l'année est optionnelle) — `heure` : `20:30` ou `20h30`, fuseau Europe/Paris.
- `lieu` : *Le Palais des Cerises* (défaut), *Opéraims* ou *Pathé Thillois* — liste modifiable
  dans le dictionnaire `LIEUX` de [config.py](config.py).
- L'embed affiche titre, synopsis, affiche, cinéma, date/heure, durée, genres,
  places restantes et liste des inscrits.
- Inscription en réagissant avec 🎟️, désinscription en retirant la réaction.
  Si la séance est complète, la réaction est retirée et la personne prévenue en MP.
- `membre` : autocomplétion sur les membres du serveur ; un pseudo (nom d'utilisateur ou
  surnom, insensible à la casse), une mention ou un identifiant Discord marchent aussi.
  Si plusieurs pseudos correspondent, le bot demande l'identifiant.
- `/cinema inscrire` passe outre le nombre de places (avec un avertissement) et prévient
  la personne en MP ; le bot ne pouvant pas réagir à sa place, elle n'aura pas de réaction
  sur l'annonce et c'est `/cinema desinscrire` qui la retire.

# Structure

```
bot.py                    point d'entrée, chargement des cogs et synchro des commandes
config.py                 .env, fuseau horaire, cinémas, emoji d'inscription
storage.py                persistance JSON atomique
tmdb.py                   client TMDB (recherche, détails, affiches)
cogs/reaction_roles.py    rôles-réaction
cogs/cinema.py            séances, embeds, inscriptions
data/                     état persisté (reaction_roles.json, screenings.json)
Dockerfile / compose.yaml conteneurisation
```
