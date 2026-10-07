"""The agent service's HTTP face: health probes, metrics, and runs on demand.
The real work, consuming incidents and approval decisions, starts and stops with the app."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from relay_agent.config import Settings
from relay_agent.graph.schemas import Incident
from relay_agent.llm.factory import MissingCredentials, make_provider
from relay_agent.recording import RUN_ID, load
from relay_agent.service.approvals import ApprovalConsumer, ApprovalDecision
from relay_agent.service.checkpoints import RedisCheckpoints
from relay_agent.service.consumer import AlreadyRunning, Busy, IncidentConsumer, Run
from relay_agent.service.postmortems import PostmortemConsumer
from relay_agent.service.runner import Runner

log = logging.getLogger(__name__)

# redis-py times reads out after 5 s by default, the same as the consumer's
# blocking read: every idle XREADGROUP would time out instead of returning empty.
REDIS_SOCKET_TIMEOUT_S = 15.0


def redis_client(url: str) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=REDIS_SOCKET_TIMEOUT_S,
        socket_connect_timeout=5.0,
        health_check_interval=30,
    )


def create_app(
    settings: Settings | None = None,
    *,
    redis: Redis | None = None,
    run: Run | None = None,
    resume: Callable[[ApprovalDecision], Awaitable[None]] | None = None,
) -> FastAPI:
    """`redis`, `run` and `resume` replace the real ones in tests."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = redis or redis_client(settings.redis_url)
        provider, unavailable = None, None
        if run is None:
            try:
                provider = make_provider(settings.investigator, settings)
            except MissingCredentials as e:
                unavailable = str(e)
                log.error("%s; every investigation will fail until it is", e)
        checkpoints = RedisCheckpoints(settings.redis_url, settings.fixer.checkpoint_ttl_minutes)
        runner = (
            None
            if run
            else Runner(settings, client, provider, unavailable=unavailable, checkpoints=checkpoints)
        )
        consumer = IncidentConsumer(
            client, settings.streams, settings.service, run or runner, socket.gethostname()
        )
        tasks = [asyncio.create_task(consumer.run_forever(), name="incident-consumer")]
        if runner is not None and settings.fixer.enabled:
            approvals = ApprovalConsumer(client, settings.streams, runner.resume, socket.gethostname())
            tasks.append(asyncio.create_task(approvals.run_forever(), name="approval-consumer"))
        if provider is not None and settings.postmortems_enabled:
            postmortems = PostmortemConsumer(client, settings, provider, socket.gethostname())
            tasks.append(asyncio.create_task(postmortems.run_forever(), name="postmortem-consumer"))
        app.state.redis, app.state.consumer, app.state.tasks = client, consumer, tasks
        app.state.resume = resume or (
            runner.resume if runner is not None and settings.fixer.enabled else None
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await consumer.drain(settings.service.shutdown_grace_s)
            await checkpoints.aclose()
            if provider is not None:
                await provider.aclose()
            if redis is None:
                await client.aclose()

    app = FastAPI(title="Relay agent service", version="0.1.0", lifespan=lifespan)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz")
    async def healthz(request: Request) -> JSONResponse:
        """Liveness: the consumer loops are alive (they ride out Redis outages themselves)."""
        for task in request.app.state.tasks:
            if task.done():
                error = None if task.cancelled() else repr(task.exception())
                return JSONResponse({"status": f"{task.get_name()} stopped", "error": error}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/readyz")
    async def readyz(request: Request) -> JSONResponse:
        try:
            await request.app.state.redis.ping()
        except RedisError as e:
            return JSONResponse({"status": "redis unavailable", "error": str(e)}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/runs")
    async def runs(request: Request) -> list[dict[str, Any]]:
        """Investigations running now."""
        return [asdict(r) for r in request.app.state.consumer.active.values()]

    def authorize(authorization: str | None) -> None:
        token = settings.service.api_token
        if token is None or not token.get_secret_value():
            raise HTTPException(403, "disabled: set RELAY_SERVICE__API_TOKEN to enable it")
        expected = f"Bearer {token.get_secret_value()}".encode()
        if not hmac.compare_digest((authorization or "").encode(), expected):
            raise HTTPException(401, "missing or wrong bearer token", headers={"WWW-Authenticate": "Bearer"})

    @app.post("/runs", status_code=202)
    async def start_run(
        incident: Incident,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        """Investigate an incident now, outside the stream (or again)."""
        authorize(authorization)
        try:
            run_id = await request.app.state.consumer.start(incident)
        except Busy:
            raise HTTPException(429, "every run slot is busy; try again later") from None
        except AlreadyRunning as e:
            raise HTTPException(409, f"incident {incident.id} is being investigated by {e}") from None
        return {"run_id": run_id, "incident_id": incident.id}

    @app.post("/runs/{run_id}/resume")
    async def resume_run(
        run_id: str,
        decision: ApprovalDecision,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        """Apply a human's decision to the run waiting for it, outside the stream.

        platform-api sends decisions on relay.approvals; this is the same, by hand. The
        approval token is still checked where the write happens (the github MCP server),
        so a decision without a valid one can reject a proposal but never execute it.
        """
        authorize(authorization)
        if decision.run_id != run_id:
            raise HTTPException(422, f"the decision is for run {decision.run_id}, not {run_id}")
        if request.app.state.resume is None:
            raise HTTPException(409, "this service proposes no fixes (the fixer is disabled)")
        await request.app.state.resume(decision)
        return {"run_id": run_id, "approval_id": decision.approval_id, "decision": decision.decision}

    @app.get("/runs/{run_id}/recording")
    async def recording(run_id: str, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
        """A finished run's replayable recording (relay_agent.recording.load)."""
        authorize(authorization)
        root = settings.service.record_dir
        run_dir = Path(root) / run_id if root and RUN_ID.match(run_id) else None
        if run_dir is None or not (run_dir / "incident.json").exists():
            raise HTTPException(404, f"no recording of {run_id}")
        return load(run_dir)

    return app
