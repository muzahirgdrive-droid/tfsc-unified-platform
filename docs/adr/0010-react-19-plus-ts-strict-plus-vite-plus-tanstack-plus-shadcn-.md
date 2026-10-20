# ADR-0010: React 19 + TS strict + Vite + TanStack + shadcn/Tailwind v4

- Status: Accepted
- Date: 2026-10-20

## Context
Pilot was one 2,266-line App.tsx without a router or data layer.

## Decision
React 19, TypeScript strict, Vite; TanStack Query/Router/Table/Virtual; react-hook-form + zod; Radix/shadcn-style components with Tailwind v4 tokens from §22; TS API types generated with openapi-typescript.

## Consequences
Larger bundle, mitigated by route-level code splitting.
