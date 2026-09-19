"""
timekprw: the FastAPI application.

Routes are thin: they validate input through the models, hand the work
to the bridge and return its models.  Errors of every kind come back as
RFC 9457 problem details.
"""

import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from timekpr.common.constants import constants as cons
from timekpr.web import models
from timekpr.web.bridge import DaemonError

PREFIX = "/api/v1"
Username = Annotated[str, Path(min_length=1)]
GroupName = Annotated[str, Path(min_length=1, description='a group name, or "all"')]
Day = Annotated[str, Path(pattern="^([1-7]|all)$", description='ISO weekday or "all"')]

_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    421: "Misdirected Request",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def problem(status, detail=None, errors=(), applied=(), headers=None):
    body = models.Problem(
        title=_TITLES.get(status, "Error"),
        status=status,
        detail=detail,
        errors=list(errors),
        applied=list(applied),
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(exclude_defaults=True),
        media_type="application/problem+json",
        headers=headers,
    )


def is_trusted(scope, trusted_sockets):
    """Whether a request arrived over one of the trusted UNIX sockets.  The
    ASGI server reports a UNIX socket connection as server = (path, None);
    anything else, including a missing entry, is not trusted."""
    server = scope.get("server")
    return bool(server) and server[1] is None and server[0] in trusted_sockets


def is_tcp(scope):
    server = scope.get("server")
    return not server or server[1] is not None


class HostCheck:
    """Reject TCP requests whose Host header is not one of ours.  A web
    page can point a name it controls at 127.0.0.1 (DNS rebinding), but the
    browser still sends that name as the Host header."""

    def __init__(self, app, hosts):
        self._app = app
        self._hosts = {host.lower() for host in hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and is_tcp(scope):
            host = next(
                (
                    value.decode("latin-1")
                    for name, value in scope["headers"]
                    if name == b"host"
                ),
                "",
            )
            if (urlsplit("//" + host).hostname or "").lower() not in self._hosts:
                await problem(421, f"Host {host!r} is not served here")(
                    scope, receive, send
                )
                return
        await self._app(scope, receive, send)


def create_app(
    bridge, static_dir=None, token=None, allowed_hosts=None, trusted_sockets=()
):
    """Build the application.  token=None disables authentication,
    allowed_hosts=None the Host header check; requests over the UNIX
    sockets named in trusted_sockets need no token."""
    app = FastAPI(
        title="timekpr web API",
        version=cons.TK_VERSION,
        openapi_url=PREFIX + "/openapi.json",
        docs_url=PREFIX + "/docs",
        redoc_url=None,
    )
    bearer = HTTPBearer(
        auto_error=False, description="The token from timekprw's token file"
    )
    trusted_sockets = set(trusted_sockets)

    def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ):
        # a trusted UNIX socket's file permissions are its access control
        if is_trusted(request.scope, trusted_sockets):
            return
        if token is not None and (
            credentials is None
            or not secrets.compare_digest(
                credentials.credentials.encode(), token.encode()
            )
        ):
            raise HTTPException(
                401,
                "a valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # ## error handling ##

    @app.exception_handler(DaemonError)
    def daemon_error(_request, ex):
        errors = (
            [models.FieldError(field=ex.field, message=ex.detail)] if ex.field else []
        )
        return problem(ex.status, ex.detail, errors, ex.applied)

    @app.exception_handler(RequestValidationError)
    def validation_error(_request, ex):
        errors = [
            models.FieldError(
                field=".".join(str(part) for part in err["loc"][1:])
                or str(err["loc"][0]),
                message=err["msg"],
            )
            for err in ex.errors()
        ]
        return problem(400, "request validation failed", errors)

    @app.exception_handler(StarletteHTTPException)
    def http_error(_request, ex):
        return problem(ex.status_code, ex.detail, headers=ex.headers)

    # ## service ##

    @app.get(
        PREFIX + "/health",
        response_model=models.Health,
        responses={503: {"model": models.Health}},
    )
    def health():
        result = bridge.health()
        return (
            result
            if result.daemon == "ok"
            else JSONResponse(status_code=503, content=result.model_dump())
        )

    api = APIRouter(
        prefix=PREFIX,
        dependencies=[Depends(authenticate)],
        responses={401: {"model": models.Problem}, 503: {"model": models.Problem}},
    )

    @api.get("/config", response_model=models.ServerConfig)
    def get_config():
        return bridge.get_server_config()

    @api.patch(
        "/config",
        response_model=models.ServerConfig,
        responses={400: {"model": models.Problem}},
    )
    def patch_config(patch: models.ServerConfigPatch):
        return bridge.patch_server_config(patch)

    # ## users ##

    user_responses = {400: {"model": models.Problem}, 404: {"model": models.Problem}}

    @api.get("/users", response_model=list[models.UserSummary])
    def list_users(include: Annotated[str | None, Query(pattern="^status$")] = None):
        return bridge.list_users(include_status=include == "status")

    @api.get("/users/{username}", response_model=models.User, responses=user_responses)
    def get_user(username: Username):
        return bridge.get_user(username)

    @api.get(
        "/users/{username}/config",
        response_model=models.UserConfig,
        responses=user_responses,
    )
    def get_user_config(username: Username):
        return bridge.get_user_config(username)

    @api.patch(
        "/users/{username}/config",
        response_model=models.UserConfig,
        responses=user_responses,
    )
    def patch_user_config(username: Username, patch: models.UserConfigPatch):
        return bridge.patch_user_config(username, patch)

    @api.get(
        "/users/{username}/status",
        response_model=models.UserStatus,
        responses=user_responses,
    )
    def get_user_status(username: Username):
        return bridge.get_user_status(username)

    @api.put(
        "/users/{username}/config/allowed-hours/{day}",
        response_model=models.UserConfig,
        responses=user_responses,
    )
    def put_allowed_hours(
        username: Username, day: Day, entries: list[models.HourEntry]
    ):
        return bridge.set_allowed_hours(username, day, entries)

    @api.post(
        "/users/{username}/time-left",
        response_model=models.UserStatus,
        responses=user_responses,
    )
    def post_time_left(username: Username, request: models.TimeLeftRequest):
        return bridge.set_time_left(username, request)

    @api.delete(
        "/users/{username}/policy",
        status_code=204,
        response_class=Response,
        responses={404: {"model": models.Problem}},
    )
    def delete_user_policy(username: Username):
        bridge.delete_user_policy(username)

    # ## policies ##

    @api.post("/policies/migrate", response_model=models.MigrationResult)
    def migrate_policies(request: models.MigrationRequest):
        return bridge.migrate_policies(request)

    # ## groups ##

    group_responses = {400: {"model": models.Problem}, 404: {"model": models.Problem}}

    @api.get("/groups", response_model=list[models.GroupSummary])
    def list_groups():
        return bridge.list_groups()

    @api.get("/groups/{group}", response_model=models.Group, responses=group_responses)
    def get_group(group: GroupName):
        return bridge.get_group(group)

    @api.get(
        "/groups/{group}/config",
        response_model=models.GroupConfig,
        responses=group_responses,
    )
    def get_group_config(group: GroupName):
        return bridge.get_group_config(group)

    @api.patch(
        "/groups/{group}/config",
        response_model=models.GroupConfig,
        responses={400: {"model": models.Problem}},
    )
    def patch_group_config(group: GroupName, patch: models.GroupConfigPatch):
        return bridge.patch_group_config(group, patch)

    @api.put(
        "/groups/{group}/config/allowed-hours/{day}",
        response_model=models.GroupConfig,
        responses={400: {"model": models.Problem}},
    )
    def put_group_allowed_hours(
        group: GroupName, day: Day, entries: list[models.HourEntry]
    ):
        return bridge.set_group_allowed_hours(group, day, entries)

    @api.delete(
        "/groups/{group}/policy",
        status_code=204,
        response_class=Response,
        responses={404: {"model": models.Problem}},
    )
    def delete_group_policy(group: GroupName):
        bridge.delete_group_policy(group)

    app.include_router(api)

    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    if allowed_hosts is not None:
        app.add_middleware(HostCheck, hosts=allowed_hosts)

    return app
