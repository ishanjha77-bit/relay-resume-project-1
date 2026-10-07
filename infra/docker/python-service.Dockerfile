# syntax=docker/dockerfile:1
# Any Python service of the uv workspace: the agent service or an MCP server.
# Build from the repository root (see `make relay-images`):
#   docker build -f infra/docker/python-service.Dockerfile \
#     --build-arg PACKAGE=relay-agent-service --build-arg COMMAND="relay-agent serve" .
# PREPARE (optional) runs once at build time as root, e.g. to bake in a model.
ARG PYTHON=3.13

FROM python:${PYTHON}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
ARG PACKAGE
WORKDIR /src
# Third-party dependencies first: this layer is rebuilt only when the lockfile changes.
COPY pyproject.toml uv.lock ./
COPY apps/agent-service/pyproject.toml apps/agent-service/
COPY mcp-servers/mcp-kit/pyproject.toml mcp-servers/mcp-kit/
COPY mcp-servers/logs-loki/pyproject.toml mcp-servers/logs-loki/
COPY mcp-servers/metrics-prometheus/pyproject.toml mcp-servers/metrics-prometheus/
COPY mcp-servers/runbooks/pyproject.toml mcp-servers/runbooks/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package "$PACKAGE"
COPY apps/agent-service apps/agent-service
COPY mcp-servers mcp-servers
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "$PACKAGE"

FROM python:${PYTHON}-slim
RUN useradd --system --uid 10001 app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
# An optional build step, e.g. downloading a model so the service runs offline.
ARG PREPARE=""
RUN if [ -n "$PREPARE" ]; then sh -c "$PREPARE"; fi
ARG COMMAND
ENV APP_COMMAND=${COMMAND}
WORKDIR /app
USER 10001
EXPOSE 8000
# The command comes from the build; exec makes it PID 1 so it gets SIGTERM.
CMD ["sh", "-c", "exec $APP_COMMAND"]
