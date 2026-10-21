"""Pydantic v2 request/response models (ADR-0002). Response models double as field
allow-lists: agent-facing models never declare domain, project or meeting fields."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from tfsc_refine import refine_email


def _email(v: str) -> str:
    r = refine_email(v)
    if not r.value or any(i.severity == 'red' for i in r.issues) or r.alternates:
        raise ValueError('Enter one valid email address.')
    return r.value


EmailStr = Annotated[str, Field(max_length=254), AfterValidator(_email)]

Tri = bool | None


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth
class SetupIn(Model):
    setup_key: str = Field(max_length=200)
    name: str = Field(min_length=1, max_length=150)
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)


class LoginIn(Model):
    email: EmailStr
    password: str = Field(max_length=200)


class PasswordIn(Model):
    current_password: str = Field(max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class UserMe(Out):
    id: uuid.UUID
    display_name: str
    email: str
    role: str
    role_label: str
    employee_code: str | None = None
    capabilities: list[str]
    is_group_leader: bool
    agreements_access: bool
    has_photo: bool
    reset_required: bool


class SessionOut(Out):
    user: UserMe
    csrf: str


class AuthStatus(Out):
    setup_required: bool
    local_login: bool
    oidc: bool
    invitations: bool = False  # Keycloak emails invitations for new SSO users
    sentry_dsn: str | None = None  # browser error reporting, when configured


class ClockItem(Out):
    zone: str
    region: str
    city: str
    time: str
    abbreviation: str | None
    utc_offset_hours: float
    diff_vs_pakistan_hours: float
    is_business_clock: bool
    calling_hours: bool
    dst_change_in_days: int | None


class TimeOut(Out):
    server_time: str
    business_date: str
    items: list[ClockItem]


class Health(Out):
    app: str
    version: str
    mode: str
    database: str


# ------------------------------------------------------------------ users & org
class UserCreate(Model):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    display_name: str | None = Field(default=None, max_length=150)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    employee_code: str | None = Field(default=None, max_length=40)
    hired_on: date | None = None
    crm_role: Literal['none', 'sales_agent', 'supervisor', 'manager', 'director_admin', 'super_admin'] = 'sales_agent'
    manager_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    group_leader: bool = False
    domain_id: str | None = Field(default=None, max_length=60)
    project_id: str | None = Field(default=None, pattern=r'^P\d{4}$')
    vonage_extension: str | None = Field(default=None, max_length=40)
    routing_view: bool = False
    temporary_password: str | None = Field(default=None, min_length=12, max_length=200)


class UserUpdate(Model):
    version: int
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=150)
    phone: str | None = Field(default=None, max_length=40)
    employee_code: str | None = Field(default=None, max_length=40)
    crm_role: Literal['none', 'sales_agent', 'supervisor', 'manager', 'director_admin', 'super_admin'] | None = None
    manager_id: uuid.UUID | None = None
    status: Literal['active', 'suspended'] | None = None
    domain_id: str | None = Field(default=None, max_length=60)
    project_id: str | None = Field(default=None, pattern=r'^P\d{4}$')
    effective_from: date | None = None
    vonage_extension: str | None = Field(default=None, max_length=40)
    routing_view: bool | None = None
    reason: str | None = Field(default=None, max_length=2000)


class UserOut(Out):
    id: uuid.UUID
    display_name: str
    first_name: str | None
    last_name: str | None
    email: str
    phone: str | None
    employee_code: str | None
    status: str
    crm_role: str
    role_label: str
    manager_id: uuid.UUID | None
    has_photo: bool
    vonage_extension: str | None
    version: int


class UserRoutingOut(UserOut):
    domain_id: str | None
    project_id: str | None
    routing_view: bool
    agreements_access: bool


class UserPage(Out):
    items: list[UserRoutingOut | UserOut]
    next_cursor: str | None = None


class TeamCreate(Model):
    name: str = Field(min_length=1, max_length=150)
    kind: Literal['team', 'group'] = 'team'
    supervisor_id: uuid.UUID
    parent_team_id: uuid.UUID | None = None
    leader_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class TeamUpdate(Model):
    version: int
    name: str | None = Field(default=None, max_length=150)
    leader_id: uuid.UUID | None = None
    clear_leader: bool = False
    active: bool | None = None
    auto_assign: bool | None = None  # §14.4 round-robin for this team (Manager+)
    lead_capacity: int | None = Field(default=None, ge=1, le=500)


class MembershipChange(Model):
    user_id: uuid.UUID
    effective_from: date | None = None


class TeamOut(Out):
    id: uuid.UUID
    name: str
    kind: str
    supervisor_id: uuid.UUID
    leader_id: uuid.UUID | None
    parent_team_id: uuid.UUID | None
    active: bool
    member_ids: list[uuid.UUID]
    auto_assign: bool = False
    lead_capacity: int = 25
    version: int


# ------------------------------------------------------------------ settings
class DomainIn(Model):
    id: str = Field(pattern=r'^[a-z0-9_]{2,60}$')
    name: str = Field(min_length=1, max_length=150)
    legal_name: str | None = Field(default=None, max_length=200)
    tfsc_entity_key: str | None = Field(default=None, max_length=80)
    active: bool = True
    sort: int = 0


class DomainUpdate(Model):
    version: int
    name: str | None = Field(default=None, max_length=150)
    legal_name: str | None = Field(default=None, max_length=200)
    tfsc_entity_key: str | None = Field(default=None, max_length=80)
    active: bool | None = None
    sort: int | None = None


class DomainOut(Out):
    id: str
    name: str
    legal_name: str | None
    tfsc_entity_key: str | None
    active: bool
    sort: int
    version: int


class ProjectIn(Model):
    id: str = Field(pattern=r'^P\d{4}$')
    name: str | None = Field(default=None, max_length=150)
    domain_id: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = Field(default=None, max_length=150)
    owner_email: EmailStr | None = None
    owner_phone: str | None = Field(default=None, max_length=40)
    status: Literal['active', 'paused', 'closed', 'pending_configuration'] = 'pending_configuration'
    notes: str | None = Field(default=None, max_length=4000)


class ProjectUpdate(Model):
    version: int
    name: str | None = Field(default=None, max_length=150)
    domain_id: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = Field(default=None, max_length=150)
    owner_email: EmailStr | None = None
    owner_phone: str | None = Field(default=None, max_length=40)
    status: Literal['active', 'paused', 'closed', 'pending_configuration'] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class ProjectOut(Out):
    id: str
    name: str | None
    domain_id: str | None
    owner_user_id: uuid.UUID | None
    owner_name: str | None
    owner_email: str | None
    owner_phone: str | None
    status: str
    notes: str | None
    in_use: bool
    version: int


class SettingsOut(Out):
    values: dict[str, Any]


class SettingsPatch(Model):
    key: Literal['lock_windows', 'celebration', 'names', 'dialer', 'lead_protection_days', 'agent_meeting_label', 'anti_scraping']
    value: Any


class TargetIn(Model):
    call_target: int = Field(ge=1, le=2000)
    conn_target: float = Field(gt=0, le=1)
    bands: dict[str, float] | None = None


class TargetOut(Out):
    effective_date: date
    call_target: int
    conn_target: float
    bands: dict[str, float]


# ------------------------------------------------------------------ activities
class ActivityIn(Model):
    name: str = Field(max_length=300)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=300)
    called: Tri = None
    connected: Tri = None
    text_sent: Tri = None
    email_sent: Tri = None
    followup_needed: Tri = None
    next_followup: str | None = Field(default=None, max_length=40)
    remarks: str | None = Field(default=None, max_length=6000)
    owner_id: uuid.UUID | None = None


class ActivityPatch(Model):
    version: int
    reason: str | None = Field(default=None, max_length=2000)
    name: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=300)
    called: Tri = None
    connected: Tri = None
    text_sent: Tri = None
    email_sent: Tri = None
    followup_needed: Tri = None
    next_followup: str | None = Field(default=None, max_length=40)
    remarks: str | None = Field(default=None, max_length=6000)


class ReasonIn(Model):
    reason: str = Field(min_length=3, max_length=2000)


class IssueOut(Out):
    field: str
    severity: str
    code: str
    message: str


class ActivityOut(Out):
    id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: str | None = None
    business_date: date
    entry_source: str
    provider_name: str
    prospect_kind: str | None
    phone: str | None
    phone_e164: str | None
    extension: str | None
    email: str | None
    called: Tri
    connected: Tri
    text_sent: Tri
    email_sent: Tri
    followup_needed: Tri
    next_followup_at: datetime | None
    remarks: str
    state: Literal['data_hunting', 'committed', 'locked', 'deleted']
    stage: str
    points: int
    first_committed_at: datetime | None
    edit_deadline: datetime | None
    seconds_left: int | None
    conflict: str | None
    can_edit: bool
    created_at: datetime
    version: int


class ActivityRoutingOut(ActivityOut):
    domain_id: str | None
    project_id: str | None


class SaveOut(Out):
    activity: ActivityRoutingOut | ActivityOut
    duplicate_notice: str
    duplicate_severity: str | None
    issues: list[IssueOut]


class ActivityPage(Out):
    items: list[ActivityRoutingOut | ActivityOut]
    next_cursor: str | None = None


class RevisionOut(Out):
    rev: int
    changed_by: uuid.UUID
    changed_by_name: str | None
    reason: str | None
    before: dict[str, Any]
    after: dict[str, Any]
    at: datetime


class RefineIn(Model):
    rows: list[dict[str, Any]] = Field(max_length=1000)


class RefinedRowOut(Out):
    row_no: int
    refined: dict[str, Any]
    issues: list[IssueOut]
    severity: str
    precheck: str | None = None


class RefineOut(Out):
    engine: str
    rows: list[RefinedRowOut]
    counts: dict[str, int]
    ms: float


class BatchIn(Model):
    request_key: str = Field(min_length=8, max_length=80)
    rows: list[dict[str, Any]] = Field(min_length=1, max_length=1000)
    source: Literal['grid', 'import'] = 'grid'


class BatchRowResult(Out):
    row_no: int
    status: Literal['saved', 'rejected', 'review']
    activity_id: uuid.UUID | None = None
    issues: list[IssueOut] = Field(default_factory=list)
    duplicate_notice: str | None = None


class BatchOut(Out):
    batch_id: uuid.UUID
    saved: int
    rejected: int
    review: int
    rows: list[BatchRowResult]
    replayed: bool = False
    ms: float


class PrecheckOut(Out):
    state: Literal['new', 'seen', 'followup', 'strong', 'protected', 'invalid']
    label: str
    phone: str | None


# ------------------------------------------------------------------ follow-ups / pipeline
class FollowupOut(Out):
    id: uuid.UUID
    owner_id: uuid.UUID
    owner_name: str | None
    activity_id: uuid.UUID | None
    provider_name: str | None
    phone: str | None
    due_at: datetime | None
    status: str
    bucket: Literal['backlog', 'upcoming', 'due_today', 'overdue_1', 'overdue_3', 'closed']
    outcome_note: str | None
    notes: list[dict[str, Any]] = Field(default_factory=list)
    version: int


class FollowupPage(Out):
    items: list[FollowupOut]
    counts: dict[str, int]


class FollowupAction(Model):
    version: int
    action: Literal['done', 'reschedule', 'cancel', 'note']
    note: str | None = Field(default=None, max_length=4000)
    due_at: datetime | None = None


class StrongRequest(Model):
    target_window: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=4000)


class StrongDecision(Model):
    version: int
    decision: Literal['confirmed', 'rejected']
    reason: str | None = Field(default=None, max_length=2000)


class StrongComment(Model):
    body: str = Field(min_length=1, max_length=4000)


class ConvertIn(Model):
    owner_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)


class StrongOut(Out):
    id: uuid.UUID
    activity_id: uuid.UUID
    provider_name: str | None
    requested_by: uuid.UUID
    requested_by_name: str | None
    requested_at: datetime
    decision: str
    decided_at: datetime | None
    reason: str | None
    target_window: str | None
    note: str | None
    last_remarks: str | None
    comments: list[dict[str, Any]] = Field(default_factory=list)
    suggested_owner_id: uuid.UUID | None = None
    suggested_owner_rule: str | None = None
    version: int


class LeadTransitionIn(Model):
    version: int
    action: Literal['sign_up', 'reverse_sale', 'lost', 'reopen']
    reason: str | None = Field(default=None, max_length=2000)


class ReassignIn(Model):
    version: int
    to_user: uuid.UUID
    reason: str = Field(min_length=3, max_length=2000)
    incentive_split: dict[str, float]


class LeadAgentOut(Out):
    """Agent view (§14.3): no meeting details, no domain/project, no other agents' data."""
    id: uuid.UUID
    provider_name: str | None
    phone: str | None
    stage: str
    stage_label: str
    owner_id: uuid.UUID
    next_followup_at: datetime | None
    created_at: datetime
    version: int


class LeadOut(LeadAgentOut):
    owner_name: str | None
    supervisor_id: uuid.UUID | None
    qualified_by: uuid.UUID | None
    qualified_at: datetime | None
    signed_at: datetime | None
    lost_reason: str | None
    agreement_status: str | None
    agreement_locked: bool
    domain_id: str | None
    project_id: str | None
    project_owner: str | None
    attribution: list[dict[str, Any]] = Field(default_factory=list)
    meetings: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    protection: dict[str, Any] | None = None


class ProtectionIn(Model):
    action: Literal['renew', 'release']
    reason: str | None = Field(default=None, max_length=500)
    version: int


class LeadPage(Out):
    items: list[LeadOut | LeadAgentOut]
    next_cursor: str | None = None


class FlagOut(Out):
    id: uuid.UUID
    severity: str
    kind: str
    status: str
    created_at: datetime
    activity_id: uuid.UUID | None
    other_activity_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    raised_for_user: uuid.UUID | None
    raised_for_name: str | None
    other_user: uuid.UUID | None
    other_user_name: str | None
    provider_name: str | None
    resolution: str | None
    resolution_note: str | None
    age_hours: float
    message: str | None = None  # protection reviews only; other kinds keep their detail server-side
    version: int


class FlagPage(Out):
    items: list[FlagOut]
    counts: dict[str, int]


class FlagResolve(Model):
    version: int
    resolution: Literal['keep_existing_owner', 'transfer', 'shared_line', 'data_error', 'acknowledged']
    note: str = Field(min_length=3, max_length=2000)
    incentive_split: dict[str, float] | None = None


class FlagAck(Model):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class NotificationOut(Out):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class NotificationPage(Out):
    items: list[NotificationOut]
    unread: int


class IdsIn(Model):
    ids: list[uuid.UUID] = Field(max_length=500)


class OkOut(Out):
    ok: bool = True
