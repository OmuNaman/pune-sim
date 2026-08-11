"""The app: read API now, run control from Phase 4, built UI when it exists."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, ORJSONResponse

from .manager import RunManager
from .registry import RunRegistry
from .routers import diff, feed, runs, world
from .worldcache import WorldCache

# The built frontend, if it has been built. Kept out of the package so `ui/` can
# be a normal Vite project with its own toolchain.
UI_DIST = Path(__file__).resolve().parents[3] / "ui" / "dist"


def create_app(runs_root: str = "runs", cfg=None, dev: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.registry.scan()
        yield
        app.state.manager.stop_all()

    app = FastAPI(
        title="pune-sim", default_response_class=ORJSONResponse, lifespan=lifespan,
    )
    app.state.registry = RunRegistry(runs_root)
    app.state.worlds = WorldCache()
    app.state.manager = RunManager()
    app.state.cfg = cfg

    if dev:
        # Vite serves the app on 5173 and proxies /api here; in production the
        # built bundle is served below from the same origin and this is off.
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware, allow_origins=["http://localhost:5173"],
            allow_methods=["*"], allow_headers=["*"],
        )

    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(world.router, prefix="/api/runs", tags=["world"])
    app.include_router(feed.router, prefix="/api/runs", tags=["feed"])
    app.include_router(diff.router, prefix="/api", tags=["diff"])

    @app.get("/api/health")
    def health():
        return {"ok": True, "runs": len(app.state.registry.runs),
                "live": app.state.manager.any_live(),
                "ui_built": UI_DIST.exists()}

    if UI_DIST.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            """Any unmatched path is a client route — the router owns the URL."""
            candidate = UI_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(UI_DIST / "index.html")

    return app
