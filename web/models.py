"""
timekprw: request and response models for the web API.

See docs/web-api.md for the API this implements.  Field names follow that
document; the mapping onto the daemon's configuration keys lives in
bridge.py.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

# ISO weekday, 1 = Monday .. 7 = Sunday (timekpr's own convention)
Weekday = Annotated[int, Field(ge=1, le=7)]
Hour = Annotated[int, Field(ge=0, le=23)]
Minute = Annotated[int, Field(ge=0, le=60)]
Seconds = Annotated[int, Field(ge=0)]

TimeLeftOperation = Literal["add", "subtract", "set"]


class Model(BaseModel):
    """Base for every API model: unknown fields are an error"""

    model_config = ConfigDict(extra="forbid")


def partial(model, name):
    """Derive a model with every field optional (for PATCH bodies); a field
    sent as null takes the setting out of the policy (model_fields_set
    tells a null from an absent field)"""
    fields = {
        fname: (finfo.rebuild_annotation() | None, None)
        for fname, finfo in model.model_fields.items()
    }
    return create_model(
        name,
        __base__=Model,
        __doc__="Partial update: every field is optional",
        **fields,
    )


# ## user configuration ##


class HourEntry(Model):
    """One allowed hour of a day, optionally only a part of it"""

    hour: Hour
    start_minute: Minute = 0
    end_minute: Minute = 60
    unaccounted: bool = False

    @model_validator(mode="after")
    def _check_interval(self):
        if self.start_minute >= self.end_minute:
            raise ValueError("start_minute must be before end_minute")
        return self


class UserConfig(Model):
    allowed_days: list[Weekday]
    # only days present in allowed_days carry a limit (the daemon stores
    # the limits positionally against the allowed days)
    limits_per_day: dict[Weekday, Seconds]
    allowed_hours: dict[Weekday, list[HourEntry]]
    limit_per_week: Seconds
    limit_per_month: Seconds
    track_inactive: bool
    hide_tray_icon: bool


UserConfigPatch = partial(UserConfig, "UserConfigPatch")


# ## user status ##


class UserStatus(Model):
    """Counters for a user; the actual_* values exist only while the daemon
    tracks a session of the user"""

    session_active: bool
    time_spent_balance: int
    time_spent_day: int
    time_spent_week: int
    time_spent_month: int
    time_left_day: int
    time_left_continuous: int | None = None
    time_spent_session: int | None = None
    time_inactive_session: int | None = None


PolicySource = Literal["user", "group", "default"]


class UserSummary(Model):
    username: str
    full_name: str
    # "user", "group:<g1>;<g2>" or "default", as the daemon lists it
    policy_source: str
    status: UserStatus | None = None


class User(Model):
    username: str
    config: UserConfig
    status: UserStatus
    # where the effective config comes from: the user's own policy (for
    # the settings it holds, policy_settings), the merge of the policies of
    # these groups, or the defaults
    policy_source: PolicySource
    policy_groups: list[str]
    policy_settings: list[str]


class TimeLeftRequest(Model):
    operation: TimeLeftOperation
    seconds: Seconds


# ## group policies ##


class GroupSummary(Model):
    group: str
    overrides: list[str]
    # best effort: an identity provider need not enumerate a group
    members: list[str]


class GroupConfig(Model):
    """A group's policy: a user config without the tray icon (a per-user
    preference), plus the groups whose policies this one takes precedence
    over for users in both"""

    allowed_days: list[Weekday]
    limits_per_day: dict[Weekday, Seconds]
    allowed_hours: dict[Weekday, list[HourEntry]]
    limit_per_week: Seconds
    limit_per_month: Seconds
    track_inactive: bool
    overrides: list[str]


GroupConfigPatch = partial(GroupConfig, "GroupConfigPatch")


class Group(Model):
    group: str
    config: GroupConfig
    # the settings the policy holds; the rest of config are the defaults
    policy_settings: list[str]


class MigrationRequest(Model):
    """Delete the user policies that restrict nothing (left over from
    versions that created one per user); dry_run only lists them"""

    dry_run: bool = True


class MigrationResult(Model):
    users: list[str]


# ## daemon configuration ##


class ServerConfig(Model):
    log_level: Annotated[int, Field(ge=1, le=3)]
    poll_time: Annotated[int, Field(ge=1)]
    save_time: Annotated[int, Field(ge=1)]
    termination_time: Annotated[int, Field(ge=1)]
    final_warning_time: Annotated[int, Field(ge=1)]
    final_notification_time: Annotated[int, Field(ge=1)]
    session_types_tracked: list[str]
    session_types_excluded: list[str]
    users_excluded: list[str]


ServerConfigPatch = partial(ServerConfig, "ServerConfigPatch")


# ## service ##


class Health(Model):
    daemon: Literal["ok", "unreachable"]
    timekpr_version: str


class FieldError(Model):
    field: str
    message: str


class Problem(Model):
    """RFC 9457 problem details"""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    errors: list[FieldError] = []
    # fields of a PATCH that were written before a later one failed
    applied: list[str] = []
