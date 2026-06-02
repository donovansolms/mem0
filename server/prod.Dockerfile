# Production image for the Butler mem0 fork.
#
# CRITICAL: the build context must be the REPO ROOT, not server/. This mirrors
# dev.Dockerfile — it installs the LOCAL mem0/ package (editable), which shadows
# the PyPI `mem0ai` pulled in by server/requirements.txt. That local install is
# what carries the fork's fixes (e.g. the pgvector cosine-similarity fix) into
# the image. Building server/Dockerfile instead would ship upstream PyPI mem0
# without the fork changes.
#
# The only difference from dev.Dockerfile is the CMD runs without --reload.
#
# Build + push from the repo root:
#   docker build -f server/prod.Dockerfile -t registry.digitalocean.com/monorail-frontend/mem0-fork:latest .
#   docker push registry.digitalocean.com/monorail-frontend/mem0-fork:latest

FROM python:3.12

WORKDIR /app

# Poetry provides the build backend for the editable mem0 install below.
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

# Server deps first (better layer caching). This pulls in PyPI mem0ai...
COPY server/requirements.txt .
RUN pip install -r requirements.txt

# ...then install the LOCAL mem0 editable, which shadows the PyPI one. THIS is
# what carries the fork's changes (incl. the pgvector fix) into the image.
WORKDIR /app/packages
COPY pyproject.toml .
COPY poetry.lock .
COPY README.md .
COPY mem0 ./mem0
RUN pip install -e .[graph]

# Server code.
WORKDIR /app
COPY server .

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
