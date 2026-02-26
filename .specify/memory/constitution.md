<!--
Sync Impact Report
- Version change: N/A (template) → 1.0.0
- Modified principles:
	- Template Principle 1 → I. Clean Code First
	- Template Principle 2 → II. UX Simplicity and Responsive-by-Default
	- Template Principle 3 → III. Minimal Dependencies and Official SDK Preference
	- Template Principle 4 → IV. Entra ID Authentication First
	- Template Principle 5 → V. Pragmatic Testing and Signal-First Logging
- Added sections:
	- Engineering Constraints
	- Delivery Workflow and Quality Gates
- Removed sections: None
- Templates requiring updates:
	- ✅ updated: .specify/templates/plan-template.md
	- ✅ updated: .specify/templates/spec-template.md
	- ✅ updated: .specify/templates/tasks-template.md
	- ✅ not applicable: .specify/templates/commands/ (directory not present)
- Deferred TODOs: None
-->

# Agentic Inbox Processing Constitution

## Core Principles

### I. Clean Code First
All production code MUST prioritize clarity, small cohesive units, and explicit naming. Every change
MUST remove or avoid dead code, avoid unnecessary indirection, and keep control flow easy to follow.
Code reviews MUST reject changes that increase complexity without a demonstrated operational benefit.
Rationale: maintainable code reduces defects and onboarding time in an evolving agentic workflow.

### II. UX Simplicity and Responsive-by-Default
User-facing flows MUST implement the simplest interaction model that satisfies the requirement and
MUST avoid unnecessary screens, controls, or states. Web interfaces MUST remain usable across common
desktop and mobile viewport sizes and MUST preserve key actions without horizontal scrolling.
Rationale: the system is operational tooling; fast, predictable interaction is more valuable than
feature-heavy interfaces.

### III. Minimal Dependencies and Official SDK Preference
New dependencies MUST be introduced only when they provide clear net value over built-in or existing
project capabilities. For Azure and Microsoft services, the official Python SDK MUST be used when
available and stable for the required scenario. A non-official or custom client MAY be used only when
the official SDK is demonstrably buggy or missing critical capabilities, and that exception MUST be
documented in the change notes.
Rationale: dependency minimization reduces attack surface and maintenance cost, while official SDKs
improve compatibility and supportability.

### IV. Entra ID Authentication First
Authentication and service-to-service access MUST default to Microsoft Entra ID (managed identity,
workload identity, or user delegated identity as appropriate). Shared keys, client secrets, and other
static credentials MUST be treated as fallback mechanisms and used only when Entra-based access is not
supported by the target service or environment constraints are explicit.
Rationale: identity-based access improves security posture, rotation hygiene, and auditability.

### V. Pragmatic Testing and Signal-First Logging
Each meaningful code change MUST include focused verification (unit, integration, or smoke) covering
the changed behavior, but test scope MUST remain proportional to risk and delivery goals. Implementations
MUST prefer structured, purposeful logging over verbose print-style output; debug noise and excessive
print statements are prohibited in production paths.
Rationale: targeted tests and high-signal logs maximize delivery speed while preserving reliability.

## Engineering Constraints

- Python implementations SHOULD favor standard library features before adding external packages.
- Logging MUST use framework or standard logging facilities with severity levels instead of free-form
	print flooding.
- Responsive behavior MUST be validated during feature completion for any modified UI templates.
- Any authentication exception to Entra ID-first policy MUST include a documented justification and
	compensating controls.

## Delivery Workflow and Quality Gates

1. Every implementation plan MUST include a constitution check covering clean code, simple UX,
	 responsiveness, dependency impact, authentication method, and test scope.
2. Every specification MUST capture UX simplicity expectations, responsive requirements where applicable,
	 authentication approach, and dependency constraints.
3. Every task list MUST include explicit tasks for dependency review, auth implementation/verification,
	 and proportional testing.
4. Pull requests MUST include a short compliance statement confirming adherence to this constitution or
	 explicitly listing approved exceptions.

## Governance

This constitution is the highest-priority engineering policy for this repository. If lower-level
guidance conflicts with it, this constitution prevails.

Amendments require: (1) a written proposal describing the change and rationale, (2) explicit update of
impacted templates under `.specify/templates/`, and (3) a version bump justified by semantic impact.

Versioning policy:
- MAJOR: backward-incompatible governance changes or principle removals/redefinitions.
- MINOR: new principle/section or materially expanded obligations.
- PATCH: clarifications, wording improvements, and non-semantic refinements.

Compliance review expectations:
- Plans, specs, tasks, and pull requests MUST pass constitution checks before implementation approval.
- Exceptions MUST be time-bounded, documented, and reviewed in the next amendment cycle.

**Version**: 1.0.0 | **Ratified**: 2026-02-26 | **Last Amended**: 2026-02-26
