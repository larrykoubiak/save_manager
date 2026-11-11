FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAVE_MANAGER_DATA=/data
WORKDIR /app
COPY pyproject.toml README.md LICENSE THIRD_PARTY.md ./
COPY save_core/ ./save_core/
COPY game_plugins/ ./game_plugins/
COPY save_web/ ./save_web/
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 savemanager \
    && useradd --uid 10001 --gid savemanager --no-create-home savemanager \
    && mkdir /data && chown savemanager:savemanager /data
USER savemanager
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/healthz', timeout=3).close()"
CMD ["save-manager-web", "--host", "0.0.0.0", "--port", "8080"]
