from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.db import get_db
from app.web.deps import LoginRequired, WEB_DIR, WorkspaceRequired, csrf_guard, render
from app.web.routes import auth, config, csv_io, disparo, empresas, filtro, hunter, inteligencia, metricas, operacao, panel, public

settings = get_settings()
settings.check_production_safety()

app = FastAPI(title="Prospector", docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(csrf_guard)])
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="prospector_session",
    same_site="lax",
    https_only=settings.session_https_only,
    max_age=12 * 3600,
)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


def _redirect(request: Request, url: str) -> Response:
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, exc: LoginRequired):
    return _redirect(request, "/login")


@app.exception_handler(WorkspaceRequired)
async def _workspace_required(request: Request, exc: WorkspaceRequired):
    return _redirect(request, "/workspaces")


@app.exception_handler(HTTPException)
async def _http_error(request: Request, exc: HTTPException):
    if request.headers.get("hx-request") or "text/html" not in request.headers.get("accept", "text/html"):
        return Response(content=str(exc.detail), status_code=exc.status_code, media_type="text/plain")
    return render(request, "error.html", status=exc.status_code, code=exc.status_code, detail=exc.detail)


@app.get("/health", include_in_schema=False)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok", "dry_run": settings.dry_run}


app.include_router(auth.router)
app.include_router(config.router)
app.include_router(csv_io.router)
app.include_router(hunter.router)
app.include_router(empresas.router)
app.include_router(filtro.router)
app.include_router(inteligencia.router)
app.include_router(disparo.router)
app.include_router(metricas.router)
app.include_router(operacao.router)
app.include_router(public.router)
app.include_router(panel.router)  # por último: tem rota genérica /{secao}
