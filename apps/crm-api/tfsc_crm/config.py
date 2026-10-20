"""Runtime configuration from environment variables (never from the repository)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ('1', 'true', 'yes', 'on')


@dataclass(frozen=True)
class Settings:
    mode: str = field(default_factory=lambda: os.getenv('CRM_MODE', 'local'))  # local | hosted | test
    origin: str = field(default_factory=lambda: os.getenv('CRM_ORIGIN', 'http://127.0.0.1:8766').rstrip('/'))
    database_url: str = field(default_factory=lambda: os.getenv('CRM_DATABASE_URL', 'postgresql+psycopg://postgres@127.0.0.1:54329/crm'))
    db_app_role: str | None = field(default_factory=lambda: os.getenv('CRM_DB_APP_ROLE', 'crm_app') or None)
    redis_url: str | None = field(default_factory=lambda: os.getenv('CRM_REDIS_URL') or None)
    phone_key_secret: str = field(default_factory=lambda: os.getenv('CRM_PHONE_KEY_SECRET', ''))
    field_key: str = field(default_factory=lambda: os.getenv('CRM_FIELD_KEY', ''))  # base64 32-byte AES key
    bridge_secret: str = field(default_factory=lambda: os.getenv('CRM_TFSC_BRIDGE_SECRET', ''))
    tfsc_url: str = field(default_factory=lambda: os.getenv('CRM_TFSC_URL', '').rstrip('/'))
    session_idle_minutes: int = field(default_factory=lambda: int(os.getenv('CRM_SESSION_IDLE_MINUTES', '30')))
    session_absolute_hours: int = field(default_factory=lambda: int(os.getenv('CRM_SESSION_ABSOLUTE_HOURS', '12')))
    allow_local_login: bool = field(default_factory=lambda: _bool('CRM_ALLOW_LOCAL_LOGIN', True))
    web_dist: str = field(default_factory=lambda: os.getenv('CRM_WEB_DIST', ''))
    sentry_dsn: str = field(default_factory=lambda: os.getenv('CRM_SENTRY_DSN', ''))
    # OIDC / Keycloak (§21). Empty issuer = SSO off (local break-glass login only).
    oidc_issuer: str = field(default_factory=lambda: os.getenv('CRM_OIDC_ISSUER', '').rstrip('/'))
    oidc_internal_url: str = field(default_factory=lambda: os.getenv('CRM_OIDC_INTERNAL_URL', '').rstrip('/'))
    oidc_client_id: str = field(default_factory=lambda: os.getenv('CRM_OIDC_CLIENT_ID', ''))
    oidc_client_secret: str = field(default_factory=lambda: os.getenv('CRM_OIDC_CLIENT_SECRET', ''))
    oidc_mfa_acr: str = field(default_factory=lambda: os.getenv('CRM_OIDC_MFA_ACR', 'mfa,2'))
    oidc_state_secret: str = field(default_factory=lambda: os.getenv('CRM_OIDC_STATE_SECRET', ''))
    metrics_token: str = field(default_factory=lambda: os.getenv('CRM_METRICS_TOKEN', ''))

    @property
    def hosted(self) -> bool:
        return self.mode == 'hosted'

    def validate(self) -> None:
        if self.hosted:
            missing = [n for n, v in (('CRM_PHONE_KEY_SECRET', self.phone_key_secret), ('CRM_FIELD_KEY', self.field_key)) if not v]
            if missing:
                raise RuntimeError('Hosted mode requires: ' + ', '.join(missing))
            if not self.origin.startswith('https://'):
                raise RuntimeError('Hosted mode requires an https:// CRM_ORIGIN.')


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.validate()
    return s
