"""
timekprw: the FastAPI application.

Routes are thin: they validate input through the models, hand the work
to the bridge and return its models.  Errors of every kind come back as
RFC 9457 problem details.
"""
import secrets
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from timekpr.common.constants import constants as cons
from timekpr.web import models
from timekpr.web.bridge import DaemonError

PREFIX = "/api/v1"
Username = Annotated[str, Path(min_length=1)]
Day = Annotated[str, Path(pattern="^([1-7]|all)$", description="ISO weekday or \"all\"")]

_TITLES = {400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 502: "Bad Gateway", 503: "Service Unavailable"}


def problem(status, detail=None, errors=(), applied=(), headers=None):
    body = models.Problem(title=_TITLES.get(status, "Error"), status=status, detail=detail, errors=list(errors), applied=list(applied))
    return JSONResponse(status_code=status, content=body.model_dump(exclude_defaults=True), media_type="application/problem+json", headers=headers)


def create_app(bridge, static_dir=None, token=None):
    """Build the application; token=None disables authentication"""
    app = FastAPI(title="timekpr web API", version=cons.TK_VERSION, openapi_url=PREFIX + "/openapi.json", docs_url=PREFIX + "/docs", redoc_url=None)
    bearer = HTTPBearer(auto_error=False, description="The token from timekprw's token file")

    def authenticate(credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)]):
        if token is not None and (credentials is None or not secrets.compare_digest(credentials.credentials.encode(), token.encode())):
            raise HTTPException(401, "a valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    # ## error handling ##

    @app.exception_handler(DaemonError)
    def daemon_error(_request, ex):
        errors = [models.FieldError(field=ex.field, message=ex.detail)] if ex.field else []
        return problem(ex.status, ex.detail, errors, ex.applied)

    @app.exception_handler(RequestValidationError)
    def validation_error(_request, ex):
        errors = [models.FieldError(field=".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]), message=err["msg"]) for err in ex.errors()]
        return problem(400, "request validation failed", errors)

    @app.exception_handler(StarletteHTTPException)
    def http_error(_request, ex):
        return problem(ex.status_code, ex.detail, headers=ex.headers)

    # ## service ##

    @app.get(PREFIX + "/health", response_model=models.Health, responses={503: {"model": models.Health}})
    def health():
        result = bridge.health()
        return result if result.daemon == "ok" else JSONResponse(status_code=503, content=result.model_dump())

    api = APIRouter(prefix=PREFIX, dependencies=[Depends(authenticate)], responses={401: {"model": models.Problem}, 503: {"model": models.Problem}})

    @api.get("/config", response_model=models.ServerConfig)
    def get_config():
        return bridge.get_server_config()

    @api.patch("/config", response_model=models.ServerConfig, responses={400: {"model": models.Problem}})
    def patch_config(patch: models.ServerConfigPatch):
        return bridge.patch_server_config(patch)

    # ## users ##

    user_responses = {400: {"model": models.Problem}, 404: {"model": models.Problem}}

    @api.get("/users", response_model=list[models.UserSummary])
    def list_users(include: Annotated[Optional[str], Query(pattern="^status$")] = None):
        return bridge.list_users(include_status=include == "status")

    @api.get("/users/{username}", response_model=models.User, responses=user_responses)
    def get_user(username: Username):
        return bridge.get_user(username)

    @api.get("/users/{username}/config", response_model=models.UserConfig, responses=user_responses)
    def get_user_config(username: Username):
        return bridge.get_user_config(username)

    @api.patch("/users/{username}/config", response_model=models.UserConfig, responses=user_responses)
    def patch_user_config(username: Username, patch: models.UserConfigPatch):
        return bridge.patch_user_config(username, patch)

    @api.get("/users/{username}/status", response_model=models.UserStatus, responses=user_responses)
    def get_user_status(username: Username):
        return bridge.get_user_status(username)

    @api.put("/users/{username}/config/allowed-hours/{day}", response_model=models.UserConfig, responses=user_responses)
    def put_allowed_hours(username: Username, day: Day, entries: list[models.HourEntry]):
        return bridge.set_allowed_hours(username, day, entries)

    @api.put("/users/{username}/config/playtime/activities", response_model=models.UserConfig, responses=user_responses)
    def put_playtime_activities(username: Username, activities: list[models.Activity]):
        return bridge.patch_user_config(username, models.UserConfigPatch(playtime=models.PlayTimeConfigPatch(activities=activities)))

    @api.post("/users/{username}/time-left", response_model=models.UserStatus, responses=user_responses)
    def post_time_left(username: Username, request: models.TimeLeftRequest):
        return bridge.set_time_left(username, request)

    @api.post("/users/{username}/playtime-left", response_model=models.UserStatus, responses=user_responses)
    def post_playtime_left(username: Username, request: models.TimeLeftRequest):
        return bridge.set_time_left(username, request, playtime=True)

    app.include_router(api)

    if static_dir is not None:
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")

    return app
