FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Paris

WORKDIR /app

# tzdata : l'image slim ne garantit pas /usr/share/zoneinfo, dont zoneinfo a
# besoin pour Europe/Paris (heures des séances).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt tzdata

COPY bot.py config.py storage.py tmdb.py ./
COPY cogs ./cogs

RUN useradd --create-home --uid 10001 coincoin \
    && mkdir -p /app/data \
    && chown -R coincoin:coincoin /app/data
USER coincoin

# État persisté : reaction_roles.json, screenings.json
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
