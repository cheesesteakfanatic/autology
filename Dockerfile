# OntoForge demo image — builds the wheel, installs it into a slim runtime,
# materializes the Meridian demo estate at first start, and serves the web app.
#
#   docker build -t ontoforge .
#   docker run -p 8765:8765 ontoforge
#   open http://localhost:8765
#
# The Meridian corpus regenerates deterministically from code (seed 7) inside
# the container — no fixture files ship in the wheel or the image. Mount a
# volume at /data to persist the materialized world across restarts (the
# entrypoint skips the ~2-minute demo build when /data already holds one).
#
# NOTE: validated by inspection in this environment (docker not installed on
# the development machine); the stage layout mirrors uv's documented
# python:3.12-slim multi-stage pattern.

# ---------------------------------------------------------------- build stage
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /src
# build context is kept minimal by .dockerignore (no fixtures, tests, docs)
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv build --wheel --out-dir /dist

# -------------------------------------------------------------- runtime stage
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=build /dist/*.whl /tmp/

RUN uv venv /opt/ontoforge \
    && VIRTUAL_ENV=/opt/ontoforge uv pip install --no-cache /tmp/*.whl \
    && rm /tmp/*.whl
ENV PATH="/opt/ontoforge/bin:${PATH}"

# the demo project (corpus + ledger + HEARTH world) lives here; mount a volume
# to persist it
VOLUME /data
EXPOSE 8765

# first start: regenerate the Meridian corpus from code and run the full
# generic pipeline (init -> ingest -> profile -> induce -> resolve ->
# materialize); subsequent starts reuse the materialized world.
CMD ["/bin/sh", "-c", \
     "test -f /data/state.json || ontoforge demo meridian /data; \
      exec ontoforge serve -p /data --host 0.0.0.0 --port 8765"]
