"""Parking Spot Monitor — Home Assistant add-on entry point."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
import uvicorn

from src.analyzer import analyze_all_bays, sync_addon_bays
from src.api import router as api_router
from src.config import settings
from src.database import db
from src.mqtt_publisher import mqtt_publisher, publish_all_bays_mqtt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
scheduler = AsyncIOScheduler()


class StripTrailingSlashMiddleware:
    """Normalize /api/bays/ → /api/bays before routing (avoids bad 307 behind Ingress)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if len(path) > 1 and path.endswith("/"):
                scope = {**scope, "path": path.rstrip("/")}
        await self.app(scope, receive, send)


async def _scheduled_analysis():
    logger.info("Starting scheduled parking bay analysis")
    result = await analyze_all_bays()
    logger.info("Analysis complete: %s", result)


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    os.makedirs(settings.snapshots_dir, exist_ok=True)

    await db.init()
    await sync_addon_bays()
    mqtt_publisher.connect()
    mqtt_result = await publish_all_bays_mqtt()
    if mqtt_result.get("published"):
        logger.info("MQTT startup publish: %s bay(s)", mqtt_result["published"])
    elif mqtt_result.get("error"):
        logger.warning("MQTT startup publish skipped: %s", mqtt_result["error"])

    scheduler.add_job(
        _scheduled_analysis,
        "interval",
        minutes=settings.snapshot_interval_minutes,
        id="parking_analysis",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Parking Spot Monitor started (interval=%s min, mqtt=%s, ha_token=%s)",
        settings.snapshot_interval_minutes,
        settings.mqtt_enabled,
        "set" if settings.ha_token else "MISSING",
    )

    asyncio.create_task(_scheduled_analysis())

    yield

    scheduler.shutdown(wait=False)
    mqtt_publisher.disconnect()


app = FastAPI(title="Parking Spot Monitor", lifespan=lifespan, redirect_slashes=False)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.add_middleware(StripTrailingSlashMiddleware)
app.include_router(api_router)

static_dir = os.path.join(BASE_DIR, "static")
templates_dir = os.path.join(BASE_DIR, "templates")
app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/data", StaticFiles(directory=settings.data_dir), name="data")
templates = Jinja2Templates(directory=templates_dir)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # HA Ingress sends this header; without it relative fetch() hits <base href="http://.../">.
    ingress_path = (request.headers.get("X-Ingress-Path") or "").rstrip("/")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "ingress_path": ingress_path},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


def main():
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
