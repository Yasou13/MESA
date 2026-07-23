# ADR 0011: Tenant and dataset authorization

- Status: Accepted

## Context

An agent ID describes a compute/persona context. Treating a client-provided
agent or session ID as the tenant boundary allowed ambiguous ownership and
made dataset-scoped provenance impossible.

## Decision

V4 authorization resolves principal→tenant→workspace→dataset→agent→session on
the server. Roles are inherited `OWNER`, `WRITER`, `READER`; purge and rollback
require explicit permissions. Server-created sessions bind an immutable
dataset set. DAO storage filters use the verified scope.

## Consequences

New deployments must provision keys, roles and agent permissions before v4
catalog/session calls. Request metadata cannot grant authority.

## Rollback

Disable v4 and use the v3 lexical-core compatibility API. Weakening the v4
boundary is not a supported rollback.
