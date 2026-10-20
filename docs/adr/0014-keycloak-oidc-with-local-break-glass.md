# ADR-0014: Keycloak OIDC with local break-glass

- Status: Accepted
- Date: 2026-10-20

## Context
Invitations, password recovery and MFA are required; both sites share identity.

## Decision
Keycloak realm (export in `infra/keycloak`). PKCE; MFA mandatory for Supervisor+; users linked by immutable (issuer, subject). Local password login remains for Super Admin break-glass only. Until Keycloak is configured the pilot local login stays available and is reported as such.

## Consequences
Requires the owner to provide domain/SMTP for invitations.
