"""FastAPI entrypoint for the AI short-drama studio."""

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.api.context import BASE_DIR, job_store
from src.api.routes import (
    assets as assets_routes,
    audio as audio_routes,
    compatibility as compatibility_routes,
    health as health_routes,
    jobs as jobs_routes,
    production as production_routes,
    projects as projects_routes,
    scripts as scripts_routes,
    settings as settings_routes,
    visual_styles as visual_styles_routes,
)


SESSION_COOKIE = "studio_session"
SESSION_MAX_AGE_SEC = 60 * 60 * 24
FRONTEND_CACHE_CONTROL = "no-store, max-age=0, must-revalidate"


class StudioStaticFiles(StaticFiles):
    """Keep the local development frontend in sync with the running server."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = FRONTEND_CACHE_CONTROL
        return response


def _auth_enabled() -> bool:
    flag = os.getenv("STUDIO_AUTH_ENABLED", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(os.getenv("STUDIO_PASSWORD", "").strip()) or flag in {"1", "true", "yes", "on"}


def _auth_username() -> str:
    return os.getenv("STUDIO_USERNAME", "admin").strip() or "admin"


def _auth_password() -> str:
    return os.getenv("STUDIO_PASSWORD", "").strip()


def _auth_secret() -> bytes:
    secret = os.getenv("STUDIO_AUTH_SECRET", "").strip() or _auth_password()
    return secret.encode("utf-8")


def _sign_payload(payload: str) -> str:
    return hmac.new(_auth_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_session_token(username: str) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + SESSION_MAX_AGE_SEC,
        "n": secrets.token_urlsafe(12),
    }
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    encoded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{encoded}.{_sign_payload(encoded)}"


def _valid_session_token(token: str) -> bool:
    try:
        encoded, signature = token.rsplit(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign_payload(encoded)):
        return False
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    return payload.get("u") == _auth_username() and int(payload.get("exp", 0)) > int(time.time())


def _login_page(error: str = "", next_url: str = "/") -> HTMLResponse:
    error_html = f'<div class="error">{error}</div>' if error else ""
    safe_next = next_url if next_url.startswith("/") else "/"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Short Drama Studio Login</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: #111827;
      color: #f9fafb;
    }}
    main {{
      width: min(420px, calc(100vw - 32px));
      border: 1px solid #374151;
      border-radius: 8px;
      padding: 28px;
      background: #1f2937;
      box-shadow: 0 18px 48px rgba(0,0,0,.32);
    }}
    h1 {{ margin: 0 0 20px; font-size: 24px; }}
    label {{ display: block; margin: 14px 0 6px; color: #d1d5db; font-size: 14px; }}
    input {{
      width: 100%;
      height: 42px;
      border: 1px solid #4b5563;
      border-radius: 6px;
      padding: 0 12px;
      background: #111827;
      color: #f9fafb;
      font-size: 15px;
    }}
    button {{
      width: 100%;
      height: 42px;
      margin-top: 20px;
      border: 0;
      border-radius: 6px;
      background: #f97316;
      color: white;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
    }}
    .error {{
      margin-bottom: 12px;
      padding: 10px 12px;
      border-radius: 6px;
      background: #7f1d1d;
      color: #fee2e2;
      font-size: 14px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Short Drama Studio</h1>
    {error_html}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <label for="username">用户名</label>
      <input id="username" name="username" autocomplete="username" required autofocus>
      <label for="password">密码</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登录</button>
    </form>
  </main>
</body>
</html>"""
    )


async def _handle_login(request: Request):
    if request.method == "GET":
        return _login_page(next_url=request.query_params.get("next", "/"))
    form = parse_qs((await request.body()).decode("utf-8", errors="ignore"))
    username = (form.get("username") or [""])[0]
    password = (form.get("password") or [""])[0]
    next_url = (form.get("next") or ["/"])[0]
    if hmac.compare_digest(username, _auth_username()) and hmac.compare_digest(password, _auth_password()):
        response = RedirectResponse(next_url if next_url.startswith("/") else "/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            _make_session_token(username),
            max_age=SESSION_MAX_AGE_SEC,
            httponly=True,
            samesite="lax",
        )
        return response
    return _login_page("Invalid username or password", next_url=next_url)


def _handle_logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


def _is_authenticated(request: Request) -> bool:
    return _valid_session_token(request.cookies.get(SESSION_COOKIE, ""))


async def _auth_middleware(request: Request, call_next):
    if not _auth_enabled():
        return await call_next(request)
    path = request.url.path
    if path == "/login":
        return await _handle_login(request)
    if path == "/logout":
        return _handle_logout()
    if path == "/favicon.ico":
        return await call_next(request)
    if not _is_authenticated(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        return RedirectResponse(f"/login?next={quote(path or '/')}", status_code=303)
    return await call_next(request)


def create_app() -> FastAPI:
    application = FastAPI(
        title="AI 短剧创作平台 API",
        description="7 Agent 协作短剧生成管线 REST API",
        version="1.0.0",
    )
    application.middleware("http")(_auth_middleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:18888", "http://localhost:18888",
            "http://127.0.0.1:17900", "http://localhost:17900",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    output_dir = BASE_DIR / "output"
    output_dir.mkdir(exist_ok=True)
    application.mount("/output", StudioStaticFiles(directory=str(output_dir)), name="output_files")
    web_dir = BASE_DIR / "web"
    if web_dir.is_dir():
        mimetypes.add_type("image/webp", ".webp")
        application.mount("/static", StudioStaticFiles(directory=str(web_dir)), name="studio_static")
    for domain_router in (
        health_routes.router, scripts_routes.router, assets_routes.router,
        production_routes.router, audio_routes.router, jobs_routes.router,
        compatibility_routes.router, projects_routes.router, settings_routes.router,
        visual_styles_routes.router,
    ):
        application.include_router(domain_router)

    @application.get("/", include_in_schema=False)
    async def serve_studio():
        return FileResponse(
            BASE_DIR / "studio.html",
            headers={"Cache-Control": FRONTEND_CACHE_CONTROL},
        )

    return application


app = create_app()


def _select_port(start_port: int = 18888, attempts: int = 10) -> int:
    for offset in range(attempts):
        candidate = start_port + offset
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", candidate))
            return candidate
        except OSError:
            pass
        finally:
            sock.close()
    return start_port


def _preferred_port() -> int:
    try:
        return int(os.getenv("SHORT_DRAMA_PORT", "18888"))
    except ValueError:
        return 18888


if __name__ == "__main__":
    import uvicorn

    job_store.interrupt_incomplete()
    settings_routes._restore_env_from_settings()
    settings_routes._reload_env_and_config()
    port = _select_port(_preferred_port())
    print(f"[AI Short Drama API] http://127.0.0.1:{port}")
    print(f"[Frontend] http://127.0.0.1:{port}")
    print(f"[API Docs] http://127.0.0.1:{port}/docs")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
