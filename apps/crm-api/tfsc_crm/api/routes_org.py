"""People, teams, groups and photos (§6-§8)."""
from __future__ import annotations

import hashlib
import io
import uuid
from datetime import date

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import Connection, func, or_, select, update

from ..db import models as m
from ..db.session import read, tx
from ..domain import audit, notify
from ..ids import uuid7
from ..pacific import business_date, utcnow
from ..security.auth import hash_password
from ..security.roles import MANAGER, ROLE_LABEL, SUPER, SUPERVISOR, Actor, can_grant_role, in_scope, scope_ids
from .deps import ActorDep, client_ip, decode_cursor, encode_cursor, parse_uuid
from .schemas import MembershipChange, OkOut, TeamCreate, TeamOut, TeamUpdate, UserCreate, UserOut, UserPage, UserRoutingOut, UserUpdate

router = APIRouter(prefix='/api', tags=['people'])


def user_out(a: Actor, r: dict, grants: set[uuid.UUID] | None = None) -> UserOut | UserRoutingOut:
    base = dict(id=r['id'], display_name=r['display_name'], first_name=r['first_name'], last_name=r['last_name'], email=r['email'],
                phone=r['phone'], employee_code=r['employee_code'], status=r['status'], crm_role=r['crm_role'],
                role_label=ROLE_LABEL.get(r['crm_role'], r['crm_role']), manager_id=r['manager_id'], has_photo=bool(r['photo_object_key']),
                vonage_extension=r['vonage_extension'], version=r['version'])
    if a.can('routing.view'):
        return UserRoutingOut(**base, domain_id=r['default_domain_id'], project_id=r['default_project_id'], routing_view=r['routing_view'],
                              agreements_access=bool(grants and r['id'] in grants))
    return UserOut(**base)


def _check_domain_project(c: Connection, domain_id: str | None, project_id: str | None) -> tuple[str, str | None]:
    domain_id = domain_id or 'scribe_align'  # §8: default domain when nothing is chosen
    if not c.execute(select(m.domains.c.id).where(m.domains.c.id == domain_id, m.domains.c.active.is_(True))).first():
        raise HTTPException(400, 'Choose an active domain.')
    if project_id:
        p = c.execute(select(m.projects.c.domain_id, m.projects.c.status).where(m.projects.c.id == project_id)).first()
        if not p or p.status == 'closed':
            raise HTTPException(400, 'Choose an open project.')
        if p.domain_id and p.domain_id != domain_id:
            raise HTTPException(400, 'That project belongs to a different domain.')
    return domain_id, project_id


@router.get('/users', response_model=UserPage)
def list_users(a: ActorDep, q: str | None = None, cursor: str | None = None, limit: int = 100, role: str | None = None,
               status: str | None = None) -> UserPage:
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')
    limit = max(1, min(limit, 200))
    u = m.users
    with read(a.org_id, a.id) as c:
        stmt = select(u).where(u.c.org_id == a.org_id, u.c.id.in_(scope_ids(a)))
        if q:
            like = f'%{q.strip()[:80]}%'
            stmt = stmt.where(or_(u.c.display_name.ilike(like), u.c.email.ilike(like), u.c.employee_code.ilike(like)))
        if role:
            stmt = stmt.where(u.c.crm_role == role)
        if status:
            stmt = stmt.where(u.c.status == status)
        cur = decode_cursor(cursor)
        if cur:
            stmt = stmt.where(or_(u.c.display_name > cur[0], (u.c.display_name == cur[0]) & (u.c.id > uuid.UUID(cur[1]))))
        rows = [dict(r) for r in c.execute(stmt.order_by(u.c.display_name, u.c.id).limit(limit + 1)).mappings()]
        grants = {r[0] for r in c.execute(select(m.module_grants.c.user_id).where(m.module_grants.c.module == 'agreements', m.module_grants.c.revoked_at.is_(None)))}
    nxt = encode_cursor(rows[limit - 1]['display_name'], rows[limit - 1]['id']) if len(rows) > limit else None
    return UserPage(items=[user_out(a, r, grants) for r in rows[:limit]], next_cursor=nxt)


@router.get('/users/{user_id}', response_model=UserRoutingOut | UserOut)
def get_user(user_id: str, a: ActorDep):
    uid = parse_uuid(user_id)
    with read(a.org_id, a.id) as c:
        if uid != a.id and not in_scope(c, a, uid):
            raise HTTPException(404, 'User not found.')
        r = c.execute(select(m.users).where(m.users.c.id == uid)).mappings().first()
        grants = {r2[0] for r2 in c.execute(select(m.module_grants.c.user_id).where(m.module_grants.c.user_id == uid, m.module_grants.c.revoked_at.is_(None)))}
    if not r:
        raise HTTPException(404, 'User not found.')
    return user_out(a, dict(r), grants)


@router.post('/users', response_model=UserRoutingOut | UserOut)
def create_user(body: UserCreate, a: ActorDep, request: Request):
    if a.rank < MANAGER:
        raise HTTPException(403, 'Supervisors can request new users; a Manager or above creates them.')
    if not can_grant_role(a, body.crm_role):
        raise HTTPException(403, 'You cannot create a user with that role.')
    uid = uuid7()
    with tx(a.org_id, a.id) as c:
        if c.execute(select(m.users.c.id).where(m.users.c.email == body.email)).first():
            raise HTTPException(409, 'A user with this email already exists.')
        manager_id = body.manager_id or a.id
        if not in_scope(c, a, manager_id):
            raise HTTPException(403, 'The reporting manager must be in your scope.')
        domain_id, project_id = _check_domain_project(c, body.domain_id, body.project_id)
        if a.rank == MANAGER and (body.routing_view or body.crm_role != 'sales_agent'):
            raise HTTPException(403, 'Managers create sales agents only.')
        display = body.display_name or f'{body.first_name} {body.last_name}'
        temp = body.temporary_password
        c.execute(m.users.insert().values(
            id=uid, org_id=a.org_id, email=body.email, display_name=display, first_name=body.first_name, last_name=body.last_name,
            phone=body.phone, employee_code=body.employee_code, hired_on=body.hired_on, crm_role=body.crm_role, manager_id=manager_id,
            default_domain_id=domain_id, default_project_id=project_id, vonage_extension=body.vonage_extension,
            routing_view=body.routing_view and body.crm_role == 'supervisor', status='active' if temp else 'invited',
            mfa_required=body.crm_role in ('supervisor', 'manager', 'director_admin', 'super_admin'),
            password_hash=hash_password(temp) if temp else None, reset_required=bool(temp)))
        c.execute(m.user_domain_assignments.insert().values(id=uuid7(), org_id=a.org_id, user_id=uid, domain_id=domain_id, project_id=project_id,
                                                            effective_from=business_date(), set_by=a.id))
        if project_id:
            c.execute(update(m.projects).where(m.projects.c.id == project_id, m.projects.c.first_used_at.is_(None)).values(first_used_at=utcnow()))
        for team_id in [t for t in (body.team_id, body.group_id) if t]:
            _add_member(c, a, team_id, uid, business_date())
        if body.group_id and body.group_leader:
            c.execute(update(m.teams).where(m.teams.c.id == body.group_id).values(leader_id=uid))
        audit.record(c, a.org_id, a.id, 'user.created', 'user', uid, None,
                     {'email': body.email, 'role': body.crm_role, 'manager_id': str(manager_id), 'domain_id': domain_id, 'project_id': project_id},
                     ip=client_ip(request))
        r = dict(c.execute(select(m.users).where(m.users.c.id == uid)).mappings().one())
    return user_out(a, r, set())


@router.patch('/users/{user_id}', response_model=UserRoutingOut | UserOut)
def update_user(user_id: str, body: UserUpdate, a: ActorDep):
    uid = parse_uuid(user_id)
    if a.rank < MANAGER and uid != a.id:
        raise HTTPException(403, 'You do not have permission for this action.')
    with tx(a.org_id, a.id) as c:
        r = c.execute(select(m.users).where(m.users.c.id == uid).with_for_update()).mappings().first()
        if not r or (uid != a.id and not in_scope(c, a, uid)):
            raise HTTPException(404, 'User not found.')
        if r['version'] != body.version:
            raise HTTPException(409, 'This user changed. Refresh first.')
        if uid != a.id and a.rank < SUPER and r['crm_role'] in ('director_admin', 'super_admin') and a.rank <= {'director_admin': 3, 'super_admin': 4}[r['crm_role']]:
            raise HTTPException(403, 'You cannot change a user at or above your own level.')
        vals: dict = {}
        self_only = uid == a.id and a.rank < MANAGER
        for k in ('first_name', 'last_name', 'display_name', 'phone'):
            if getattr(body, k) is not None:
                vals[k] = getattr(body, k)
        if not self_only:
            for k in ('employee_code', 'vonage_extension'):
                if getattr(body, k) is not None:
                    vals[k] = getattr(body, k)
            if body.crm_role and body.crm_role != r['crm_role']:
                if uid == a.id or not can_grant_role(a, body.crm_role):
                    raise HTTPException(403, 'You cannot assign that role.')
                vals['crm_role'] = body.crm_role
                vals['mfa_required'] = body.crm_role in ('supervisor', 'manager', 'director_admin', 'super_admin')
            if body.manager_id and body.manager_id != r['manager_id']:
                if body.manager_id == uid or not in_scope(c, a, body.manager_id):
                    raise HTTPException(403, 'The reporting manager must be in your scope.')
                vals['manager_id'] = body.manager_id
            if body.status and body.status != r['status']:
                if uid == a.id:
                    raise HTTPException(400, 'You cannot suspend yourself.')
                vals['status'] = body.status
            if body.routing_view is not None:
                vals['routing_view'] = body.routing_view
            if body.domain_id or body.project_id:
                if not a.can('routing.view'):
                    raise HTTPException(403, 'You do not have permission for this action.')
                domain_id, project_id = _check_domain_project(c, body.domain_id or r['default_domain_id'], body.project_id or r['default_project_id'])
                eff = body.effective_from or business_date()
                c.execute(update(m.user_domain_assignments).where(m.user_domain_assignments.c.user_id == uid, m.user_domain_assignments.c.effective_to.is_(None))
                          .values(effective_to=eff))
                c.execute(m.user_domain_assignments.insert().values(id=uuid7(), org_id=a.org_id, user_id=uid, domain_id=domain_id, project_id=project_id,
                                                                    effective_from=eff, set_by=a.id))
                vals.update(default_domain_id=domain_id, default_project_id=project_id)
        if not vals:
            return user_out(a, dict(r))
        vals.update(version=r['version'] + 1, updated_at=utcnow())
        c.execute(update(m.users).where(m.users.c.id == uid).values(**vals))
        if vals.get('status') == 'suspended':
            c.execute(update(m.user_sessions).where(m.user_sessions.c.user_id == uid, m.user_sessions.c.revoked_at.is_(None)).values(revoked_at=utcnow()))
        audit.record(c, a.org_id, a.id, 'user.updated', 'user', uid, {k: str(r[k]) for k in vals if k in r}, {k: str(v) for k, v in vals.items()})
        fresh = dict(c.execute(select(m.users).where(m.users.c.id == uid)).mappings().one())
    return user_out(a, fresh)


@router.post('/users/request', response_model=OkOut)
def request_user(body: UserCreate, a: ActorDep) -> OkOut:
    """Supervisors request new users; their manager is notified (§6.2 'can request')."""
    a.require('users.request')
    with tx(a.org_id, a.id) as c:
        managers = notify.supervisors_of(c, a.id)
        notify.notify(c, a.org_id, managers or notify.super_admins(c, a.org_id), 'user.requested',
                      {'requested_by': a.display_name, 'email': body.email, 'name': f'{body.first_name} {body.last_name}', 'role': body.crm_role})
        audit.record(c, a.org_id, a.id, 'user.requested', 'user', None, None, {'email': body.email})
    return OkOut()


# ------------------------------------------------------------------ photos
@router.post('/users/{user_id}/photo', response_model=OkOut)
async def upload_photo(user_id: str, a: ActorDep, file: UploadFile = File(...)) -> OkOut:
    uid = parse_uuid(user_id)
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, 'Use an image smaller than 5 MB.')
    try:
        from PIL import Image, ImageOps
        src = Image.open(io.BytesIO(data))
        if src.width * src.height > 40_000_000:
            raise ValueError
        rgb = ImageOps.exif_transpose(src).convert('RGB')  # re-encoded: EXIF and metadata are dropped
        side = min(rgb.size)
        left, top = (rgb.width - side) // 2, (rgb.height - side) // 2
        square = rgb.crop((left, top, left + side, top + side)).resize((384, 384))
        out = io.BytesIO()
        square.save(out, format='JPEG', quality=86)
        jpg = out.getvalue()
    except Exception:
        raise HTTPException(400, 'Use a PNG, JPEG or WebP image.') from None
    with tx(a.org_id, a.id) as c:
        if uid != a.id and (a.rank < MANAGER or not in_scope(c, a, uid)):
            raise HTTPException(403, 'You can only change your own photo.')
        key = f'photos/{uid}.jpg'
        from sqlalchemy.dialects.postgresql import insert
        ins = insert(m.stored_files).values(key=key, org_id=a.org_id, content_type='image/jpeg', size=len(jpg),
                                                    sha256=hashlib.sha256(jpg).hexdigest(), data=jpg)
        c.execute(ins.on_conflict_do_update(index_elements=['key'], set_={'data': jpg, 'size': len(jpg), 'sha256': hashlib.sha256(jpg).hexdigest()}))
        c.execute(update(m.users).where(m.users.c.id == uid).values(photo_object_key=key))
        audit.record(c, a.org_id, a.id, 'user.photo_updated', 'user', uid)
    return OkOut()


@router.get('/users/{user_id}/photo')
def photo(user_id: str, a: ActorDep) -> Response:
    uid = parse_uuid(user_id)
    with read(a.org_id, a.id) as c:
        key = c.execute(select(m.users.c.photo_object_key).where(m.users.c.id == uid)).scalar_one_or_none()
        if not key:
            raise HTTPException(404, 'Photo not found.')
        sf = m.stored_files
        data = c.execute(select(sf.c.data).where(sf.c.key == key)).scalar_one_or_none()
    if data is None:
        raise HTTPException(404, 'Photo not found.')
    # Photos are visible org-wide (leaderboard, celebrations); they carry no client data.
    return Response(bytes(data), media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=300'})


# ------------------------------------------------------------------ teams & groups
def _team_out(c: Connection, t: dict) -> TeamOut:
    ids = [r[0] for r in c.execute(select(m.team_memberships.c.user_id).where(m.team_memberships.c.team_id == t['id'], m.team_memberships.c.effective_to.is_(None)))]
    return TeamOut(id=t['id'], name=t['name'], kind=t['kind'], supervisor_id=t['supervisor_id'], leader_id=t['leader_id'],
                   parent_team_id=t['parent_team_id'], active=t['active'], member_ids=ids, version=t['version'])


def _team_for_update(c: Connection, a: Actor, team_id: uuid.UUID) -> dict:
    t = c.execute(select(m.teams).where(m.teams.c.id == team_id, m.teams.c.org_id == a.org_id).with_for_update()).mappings().first()
    if not t or not in_scope(c, a, t['supervisor_id']):
        raise HTTPException(404, 'Team not found.')
    if t['kind'] == 'team' and a.rank < MANAGER:
        raise HTTPException(403, 'Managers and Directors manage teams; Supervisors manage groups.')
    return dict(t)


def _add_member(c: Connection, a: Actor, team_id: uuid.UUID, user_id: uuid.UUID, eff: date) -> None:
    t = c.execute(select(m.teams).where(m.teams.c.id == team_id)).mappings().first()
    if not t:
        raise HTTPException(404, 'Team not found.')
    if not in_scope(c, a, user_id):
        raise HTTPException(403, 'That person is outside your scope.')
    if t['kind'] == 'group':
        size = c.execute(select(func.count()).select_from(m.team_memberships).where(m.team_memberships.c.team_id == team_id, m.team_memberships.c.effective_to.is_(None))).scalar_one()
        if size >= 6:
            raise HTTPException(400, 'Groups are meant for 3-4 agents (maximum 6).')
        # one active group per agent
        c.execute(update(m.team_memberships).where(m.team_memberships.c.user_id == user_id, m.team_memberships.c.effective_to.is_(None),
                                                   m.team_memberships.c.team_id.in_(select(m.teams.c.id).where(m.teams.c.kind == 'group'))).values(effective_to=eff))
    exists = c.execute(select(m.team_memberships.c.id).where(m.team_memberships.c.team_id == team_id, m.team_memberships.c.user_id == user_id,
                                                              m.team_memberships.c.effective_to.is_(None))).first()
    if not exists:
        c.execute(m.team_memberships.insert().values(id=uuid7(), org_id=a.org_id, team_id=team_id, user_id=user_id, effective_from=eff))


@router.get('/teams', response_model=list[TeamOut])
def list_teams(a: ActorDep, kind: str | None = None) -> list[TeamOut]:
    with read(a.org_id, a.id) as c:
        stmt = select(m.teams).where(m.teams.c.org_id == a.org_id)
        if a.rank < SUPERVISOR:
            stmt = stmt.where(m.teams.c.id.in_(select(m.team_memberships.c.team_id).where(m.team_memberships.c.user_id == a.id, m.team_memberships.c.effective_to.is_(None))))
        else:
            stmt = stmt.where(m.teams.c.supervisor_id.in_(scope_ids(a)))
        if kind:
            stmt = stmt.where(m.teams.c.kind == kind)
        return [_team_out(c, dict(t)) for t in c.execute(stmt.order_by(m.teams.c.name)).mappings()]


@router.post('/teams', response_model=TeamOut)
def create_team(body: TeamCreate, a: ActorDep) -> TeamOut:
    a.require('teams.manage' if body.kind == 'team' else 'groups.manage')
    tid = uuid7()
    eff = business_date()
    with tx(a.org_id, a.id) as c:
        if not in_scope(c, a, body.supervisor_id):
            raise HTTPException(403, 'The supervisor must be in your scope.')
        if a.rank == SUPERVISOR and body.supervisor_id != a.id:
            raise HTTPException(403, 'Supervisors create groups in their own team.')
        c.execute(m.teams.insert().values(id=tid, org_id=a.org_id, name=body.name, kind=body.kind, supervisor_id=body.supervisor_id,
                                          parent_team_id=body.parent_team_id))
        for uid in body.member_ids:
            _add_member(c, a, tid, uid, eff)
        if body.leader_id:
            if body.kind != 'group' or body.leader_id not in body.member_ids:
                raise HTTPException(400, 'The group leader must be a member of the group.')
            c.execute(update(m.teams).where(m.teams.c.id == tid).values(leader_id=body.leader_id))
        audit.record(c, a.org_id, a.id, f'{body.kind}.created', 'team', tid, None, body.model_dump(mode='json'))
        return _team_out(c, dict(c.execute(select(m.teams).where(m.teams.c.id == tid)).mappings().one()))


@router.patch('/teams/{team_id}', response_model=TeamOut)
def update_team(team_id: str, body: TeamUpdate, a: ActorDep) -> TeamOut:
    a.require('groups.manage')
    with tx(a.org_id, a.id) as c:
        t = _team_for_update(c, a, parse_uuid(team_id))
        if t['version'] != body.version:
            raise HTTPException(409, 'This team changed. Refresh first.')
        vals: dict = {'version': t['version'] + 1, 'updated_at': utcnow()}
        if body.name:
            vals['name'] = body.name
        if body.active is not None:
            vals['active'] = body.active
        if body.clear_leader:
            vals['leader_id'] = None
        elif body.leader_id:
            member = c.execute(select(m.team_memberships.c.id).where(m.team_memberships.c.team_id == t['id'], m.team_memberships.c.user_id == body.leader_id,
                                                                     m.team_memberships.c.effective_to.is_(None))).first()
            if t['kind'] != 'group' or not member:
                raise HTTPException(400, 'The group leader must be a member of the group.')
            vals['leader_id'] = body.leader_id
        c.execute(update(m.teams).where(m.teams.c.id == t['id']).values(**vals))
        audit.record(c, a.org_id, a.id, 'team.updated', 'team', t['id'], {'leader_id': str(t['leader_id'])}, {k: str(v) for k, v in vals.items()})
        return _team_out(c, dict(c.execute(select(m.teams).where(m.teams.c.id == t['id'])).mappings().one()))


@router.post('/teams/{team_id}/members', response_model=TeamOut)
def add_member(team_id: str, body: MembershipChange, a: ActorDep) -> TeamOut:
    a.require('groups.manage')
    with tx(a.org_id, a.id) as c:
        t = _team_for_update(c, a, parse_uuid(team_id))
        _add_member(c, a, t['id'], body.user_id, body.effective_from or business_date())
        audit.record(c, a.org_id, a.id, 'team.member_added', 'team', t['id'], None, {'user_id': str(body.user_id)})
        return _team_out(c, t)


@router.post('/teams/{team_id}/members/remove', response_model=TeamOut)
def remove_member(team_id: str, body: MembershipChange, a: ActorDep) -> TeamOut:
    a.require('groups.manage')
    with tx(a.org_id, a.id) as c:
        t = _team_for_update(c, a, parse_uuid(team_id))
        c.execute(update(m.team_memberships).where(m.team_memberships.c.team_id == t['id'], m.team_memberships.c.user_id == body.user_id,
                                                   m.team_memberships.c.effective_to.is_(None)).values(effective_to=body.effective_from or business_date()))
        if t['leader_id'] == body.user_id:
            c.execute(update(m.teams).where(m.teams.c.id == t['id']).values(leader_id=None))
        audit.record(c, a.org_id, a.id, 'team.member_removed', 'team', t['id'], None, {'user_id': str(body.user_id)})
        return _team_out(c, t)


@router.get('/people/tree')
def org_tree(a: ActorDep) -> list[dict]:
    """Org chart within scope (reporting lines)."""
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')
    with read(a.org_id, a.id) as c:
        rows = c.execute(select(m.users.c.id, m.users.c.display_name, m.users.c.crm_role, m.users.c.manager_id, m.users.c.status)
                         .where(m.users.c.id.in_(scope_ids(a))).order_by(m.users.c.display_name)).mappings().all()
    return [{'id': str(r['id']), 'name': r['display_name'], 'role': r['crm_role'], 'role_label': ROLE_LABEL.get(r['crm_role']),
             'manager_id': str(r['manager_id']) if r['manager_id'] else None, 'status': r['status']} for r in rows]
