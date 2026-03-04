# Feature Specification: Pipeline Configuration

**Feature Branch**: `002-pipeline-config`  
**Created**: 2026-03-04  
**Status**: Draft  
**Input**: User description: "Configuration capability allowing admins to set where the email processing pipeline ends: full classification or triage-only mode. Option 1 runs until email is classified and sent to a queue or sent to human-in-the-loop. Option 2 runs until the email is triaged and sent to a further processing queue, or goes to human-in-the-loop, not-relevant, or dead letter — excluding the classification step. Emails arrive as messages with payload containing email metadata: body, subject, sent timestamp, processed timestamp, attachment links, sender."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.
  
  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Deploy with Full Classification Pipeline (Priority: P1)

An admin deploys the solution and configures it to run the **full pipeline**. Emails arriving on the intake queue go through the complete processing flow: relevance triage (PE-related or not), pre-processing (attachment extraction, link downloads, OCR), full classification into a specific event type, and routing to the appropriate output queue based on confidence — either the archival-pending queue (high confidence) or the human-review queue (low confidence). Non-relevant emails are routed to the discarded queue. This is the current default behaviour and must remain the default when no explicit configuration is set.

**Why this priority**: This preserves the existing end-to-end functionality. Every other mode variation builds on top of this baseline, so it must work correctly first.

**Independent Test**: Set the pipeline mode to "full" (or leave the configuration unset/default). Send an email with attachments to the intake queue. Verify the email progresses through relevance check, attachment OCR, classification, and arrives in either the archival-pending or human-review queue with classification details populated.

**Acceptance Scenarios**:

1. **Given** the configuration is set to full-pipeline mode, **When** a PE-relevant email with attachments arrives on the intake queue, **Then** the system performs relevance triage, downloads/extracts attachments, runs OCR, classifies the email into a specific event type, and routes it to the archival-pending queue (if confidence ≥ 65%) or the human-review queue (if confidence < 65%).
2. **Given** the configuration is set to full-pipeline mode, **When** a non-PE-relevant email arrives on the intake queue, **Then** the system performs relevance triage, determines it is not relevant, and routes it to the discarded queue without proceeding to classification.
3. **Given** no explicit pipeline mode configuration exists, **When** an email arrives on the intake queue, **Then** the system behaves identically to full-pipeline mode (backward compatible default).

---

### User Story 2 - Deploy with Triage-Only Pipeline (Priority: P1)

An admin deploys the solution and configures it to run in **triage-only mode**. Emails arriving on the intake queue go through relevance triage and pre-processing (attachment extraction, link downloads, OCR) but the classification step is skipped entirely. After triage and pre-processing, relevant emails are routed to an output queue (default name: `triage-complete`) that serves as the integration point with an external document processing system (IDP). The queue name and Service Bus connection are configurable via environment variables, allowing integration with a separate Azure Service Bus namespace owned by the IDP team. Non-relevant emails still go to the discarded queue. Emails where the system lacks confidence in the relevance decision go to the human-review queue.

**Why this priority**: This is the other core operating mode requested by the user. It enables deployments where classification is handled externally or not needed, reducing processing time and cost.

**Independent Test**: Set the pipeline mode to "triage-only". Send a PE-relevant email with attachments to the intake queue. Verify the email progresses through relevance triage and attachment pre-processing but does NOT undergo classification. Verify it arrives in the `triage-complete` queue with relevance details but no classification category.

**Acceptance Scenarios**:

1. **Given** the configuration is set to triage-only mode, **When** a PE-relevant email arrives with relevance confidence ≥ 80%, **Then** the system performs relevance triage, pre-processes attachments (download, extract, OCR), and routes the email to the `triage-complete` queue. No classification step is executed.
2. **Given** the configuration is set to triage-only mode, **When** a non-PE-relevant email arrives on the intake queue, **Then** the system performs relevance triage and routes it to the discarded queue.
3. **Given** the configuration is set to triage-only mode, **When** the system's relevance confidence is below 80%, **Then** the email is routed to the human-review queue for manual review.
4. **Given** the configuration is set to triage-only mode, **When** a relevant email's pre-processing fails fatally (all attachment processing fails), **Then** the email is routed to the dead-letter queue with error context for operational investigation.

---

### User Story 3 - Admin Modifies Pipeline Configuration (Priority: P2)

An admin needs to switch the deployment from full-pipeline mode to triage-only mode (or vice versa) to adjust to changing operational needs. The admin updates the `PIPELINE_MODE` environment variable in the `.env` file, and on the next agent restart the system operates in the newly configured mode. No code changes or redeployment are required.

**Why this priority**: The ability to switch modes without code changes is what makes this a configuration capability rather than a code fork. It is secondary to the two modes actually working.

**Independent Test**: Start the system in full-pipeline mode, process an email end-to-end. Then change the configuration to triage-only mode, restart the agent, and send another email. Verify the second email skips classification and routes to the `triage-complete` queue.

**Acceptance Scenarios**:

1. **Given** the system is running in full-pipeline mode, **When** the admin changes the configuration to triage-only and restarts the agent, **Then** subsequent emails are processed in triage-only mode (no classification step).
2. **Given** the system is running in triage-only mode, **When** the admin changes the configuration to full-pipeline and restarts the agent, **Then** subsequent emails are processed with the full classification step.
3. **Given** the admin provides an invalid pipeline mode value in the configuration, **When** the system starts, **Then** it logs a clear error message indicating the invalid value and falls back to the default (full-pipeline) mode.

---

### User Story 4 - Pipeline State Visibility on Dashboard (Priority: P3)

An admin or operator viewing the web dashboard can see which pipeline mode the system is currently running in. Each processed email shows which pipeline steps were executed, so operators can verify the configuration is taking effect and diagnose processing issues.

**Why this priority**: Operational visibility is important but secondary to the modes themselves functioning correctly. The dashboard already shows email processing state; this extends it with pipeline-mode context.

**Independent Test**: Configure triage-only mode, process an email, then view the dashboard. Verify the dashboard shows the current pipeline mode and the email's processing record reflects that classification was skipped.

**Acceptance Scenarios**:

1. **Given** the system is running in triage-only mode, **When** an operator views the dashboard, **Then** the current pipeline mode is displayed.
2. **Given** an email was processed in triage-only mode, **When** an operator views the email's detail on the dashboard, **Then** the processing steps show triage and pre-processing as completed, and classification as "skipped (triage-only mode)".

---

### Edge Cases

- What happens when the `PIPELINE_MODE` environment variable is not set? The system MUST default to full-pipeline mode and log a warning that no pipeline mode was configured.
- What happens when `PIPELINE_MODE` is changed while emails are mid-processing? In-flight emails MUST complete processing under the mode that was active when they were dequeued. The new mode applies only after agent restart.
- What happens when `PIPELINE_MODE` contains an unrecognised value? The system MUST log an error, fall back to full-pipeline mode, and continue operating rather than crashing.
- What happens to the email metadata payload format — does it change between modes? The intake message payload structure (body, subject, sent timestamp, processed timestamp, attachment links, sender) MUST remain the same regardless of pipeline mode. Only the processing steps executed and the output queue routing differ.
- What happens to emails in triage-only mode that would normally go to the dead-letter queue? Emails that fail pre-processing fatally (e.g., all attachment downloads and OCR fail with unrecoverable errors) are routed to the dead-letter queue with error metadata, regardless of pipeline mode.
- What happens when `TRIAGE_COMPLETE_SB_NAMESPACE` is set but the external namespace is unreachable? The system MUST log the connection error and route the email to the dead-letter queue on the primary namespace, preserving the email for retry.
- What happens when the triage-complete queue does not exist on the target namespace? The ServiceBus SDK raises an error which the agent treats as an unrecoverable routing failure — the email is routed to the dead-letter queue on the primary namespace.
- What happens when a relevant email in triage-only mode has zero attachments? The email is processed normally — `hasAttachments` is `false`, `attachmentsCount` is `0`, `attachmentPaths` is `[]`. Pre-processing is a no-op and the email proceeds to routing.
- What happens when only some attachments fail pre-processing (partial failure)? The email continues processing with the successfully extracted attachments. Only when ALL attachment processing fails fatally is the email routed to the dead-letter queue.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support a configuration mechanism via the `PIPELINE_MODE` environment variable (read from the `.env` file), allowing admins to specify the pipeline processing mode without code changes.
- **FR-002**: The system MUST support two pipeline modes: **full-pipeline** (triage + classification) and **triage-only** (triage without classification).
- **FR-003**: When operating in **full-pipeline** mode, the system MUST execute all current processing steps: intake → relevance triage → attachment pre-processing (download, extract, OCR) → classification → routing (archival-pending or human-review) for relevant emails, and discarded queue for non-relevant emails.
- **FR-004**: When operating in **triage-only** mode, the system MUST execute: intake → relevance triage → attachment pre-processing (download, extract, OCR) → routing to the configured triage-complete queue for relevant emails (relevance confidence ≥ 80%), discarded queue for non-relevant emails, and human-review queue when relevance confidence is below 80%. The classification step (encompassing LLM classification, deduplication, and PE-event creation — implementation Steps 3–5) MUST be skipped entirely.
- **FR-014**: The output queue name for triage-only mode MUST be configurable via the `TRIAGE_COMPLETE_QUEUE` environment variable. If not set or set to an empty/whitespace-only string, it MUST default to `triage-complete`.
- **FR-015**: The system MUST support sending triage-complete messages to an external Azure Service Bus namespace via the `TRIAGE_COMPLETE_SB_NAMESPACE` environment variable (bare namespace name without the `.servicebus.windows.net` suffix; the SDK appends the suffix automatically). If not set, the system MUST use the primary Service Bus namespace (`SERVICEBUS_NAMESPACE`). Authentication to the external namespace MUST use DefaultAzureCredential (Entra ID). The deploying identity MUST have the `Azure Service Bus Data Sender` RBAC role on the target namespace.
- **FR-005**: The default pipeline mode, when no configuration is provided, MUST be **full-pipeline** to maintain backward compatibility with existing deployments.
- **FR-006**: The `PIPELINE_MODE` environment variable MUST be read at agent startup. Changes MUST take effect on the next agent restart.
- **FR-007**: The system MUST validate the configured pipeline mode at startup and log a clear error if an invalid value is detected, falling back to the default mode.
- **FR-008**: The system MUST log which pipeline mode is active at startup, including the triage-complete queue name and external namespace (if configured), so operators can confirm the configuration is correct.
- **FR-009**: Each email's processing record (stored in the data store) MUST include which pipeline mode was used during its processing, enabling audit and dashboard display.
- **FR-010**: The email intake message payload MUST contain at minimum: email body, subject, sender, sent timestamp, processed timestamp, and attachment links. This payload format MUST be consistent regardless of pipeline mode.
- **FR-011**: In triage-only mode, the system MUST still perform attachment pre-processing steps (download linked documents, extract attachments, run OCR) before routing, so that downstream processing has access to extracted content.
- **FR-012**: The system MUST route emails to the dead-letter queue when unrecoverable processing errors occur, regardless of pipeline mode. An error is unrecoverable when all processing attempts for a step have failed (e.g., all attachment downloads and OCR fail, or the target queue is unreachable after SDK-level retries).
- **FR-013**: The dashboard MUST display the current pipeline mode and reflect which steps were executed for each processed email.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: The pipeline mode selection MUST be implemented as a simple conditional check at the point where classification is invoked. Avoid creating complex strategy patterns or plugin architectures — a single configuration value driving an if/else branch is sufficient.
- **CAR-002 (UX Simplicity)**: Dashboard changes MUST be minimal — a mode indicator on the header and a "skipped" label on the classification step for triage-only emails. No new screens.
- **CAR-003 (Responsive Design)**: Any dashboard additions MUST render correctly on common viewport sizes.
- **CAR-004 (Dependencies)**: No new dependencies required. Configuration file parsing MUST use existing capabilities or standard library features.
- **CAR-005 (Auth)**: No changes to authentication. All existing Entra ID-based access patterns are preserved.
- **CAR-006 (Azure/Microsoft SDKs)**: No new Azure SDKs required. Existing Service Bus, Cosmos DB, and Storage SDKs continue to be used.
- **CAR-007 (Testing Scope)**: Tests MUST be kept to a reasonable minimum focused on simplicity. Only two core tests required: (1) triage-only mode skips classification and routes to `triage-complete`, (2) default/full mode preserves existing behaviour. No exhaustive config-parsing or edge-case tests.
- **CAR-008 (Logging Discipline)**: Pipeline mode MUST be logged at startup via structured logging. Each email processing MUST log which mode was applied. No print statements.

### Key Entities

- **Pipeline Configuration**: Environment variables specifying the processing mode and integration settings. Key variables: `PIPELINE_MODE` (`full` or `triage-only`, default: `full`), `TRIAGE_COMPLETE_QUEUE` (output queue name, default: `triage-complete`), `TRIAGE_COMPLETE_SB_NAMESPACE` (optional external Service Bus namespace for IDP integration; if unset, uses the primary namespace). All read at agent startup from the `.env` file.
- **Email Record** (existing entity, extended): The data-store record for each processed email. Extended with: `pipelineMode` (the mode active when the email was processed), `stepsExecuted` (list of processing steps completed for this email, e.g., ["triage", "pre-processing", "classification", "routing"] or ["triage", "pre-processing", "routing"]).
- **Email Intake Message**: The message payload received from the intake queue. Contains: `emailId`, `body`, `subject`, `from` (sender), `sentTimestamp`, `processedTimestamp`, `attachmentLinks`, `hasAttachments`, `attachmentsCount`. This format is fixed regardless of pipeline mode.

## Clarifications

### Session 2026-03-04

- Q: What configuration mechanism should be used — a dedicated config file, an environment variable, or both? → A: A single environment variable (`PIPELINE_MODE`) read from `.env`, consistent with existing project patterns.
- Q: What relevance confidence threshold should be used in triage-only mode to route to human-review vs. further-processing? → A: 80%. Emails with relevance confidence ≥ 80% are routed automatically; below 80% go to human-review.
- Q: What should the output queue for triaged-but-unclassified emails be named? → A: `triage-complete` (default). This queue is the integration point with an external IDP system. The queue name is configurable via `TRIAGE_COMPLETE_QUEUE` env var, and the Service Bus namespace is configurable via `TRIAGE_COMPLETE_SB_NAMESPACE` to support routing to a separate IDP-owned Service Bus instance.

## Assumptions

- The pipeline mode is configured via the `PIPELINE_MODE` environment variable, read from the `.env` file at agent startup, matching the existing pattern used for `SERVICEBUS_NAMESPACE`, `COSMOS_ENDPOINT`, etc.
- Pipeline mode is a deployment-time setting, not a per-email or per-run setting. All emails processed by a given agent instance use the same mode until the agent is restarted with new configuration.
- The `triage-complete` queue in triage-only mode is the integration point with an external IDP (Intelligent Document Processing) system. The queue name defaults to `triage-complete` but is configurable. The queue may reside on the primary Service Bus namespace or on a separate namespace owned by the IDP team.
- When `TRIAGE_COMPLETE_SB_NAMESPACE` is set, the system connects to that external namespace using DefaultAzureCredential. The deploying identity must have the required RBAC role on the external namespace.
- Existing deployments that do not set a pipeline mode will continue to operate exactly as before — no migration or config file creation is required for backward compatibility.
- The dead-letter queue behaviour for unrecoverable errors already exists in the Service Bus infrastructure and does not need to be newly created.
- The `triage-complete` queue (and any custom queue name specified via `TRIAGE_COMPLETE_QUEUE`) MUST be pre-created on the target Service Bus namespace. The agent does not auto-create queues.
- When `TRIAGE_COMPLETE_SB_NAMESPACE` is set but `PIPELINE_MODE` is `"full"`, the external namespace configuration is silently ignored. The external Service Bus client is only instantiated in triage-only mode.
- Attachment pre-processing (download, extract, OCR) runs in both modes because downstream consumers (whether automated classifiers or human reviewers) benefit from having the extracted text content available.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An admin can switch the pipeline from full mode to triage-only mode (or vice versa) by changing a single configuration value and restarting the agent, with no code changes, in under 2 minutes.
- **SC-002**: In triage-only mode, email processing time is reduced compared to full-pipeline mode (classification step is eliminated), with no emails incorrectly undergoing classification.
- **SC-003**: 100% of emails processed in triage-only mode arrive in the correct output queue (`triage-complete`, discarded, human-review, or dead-letter) without passing through the classification step.
- **SC-004**: Existing deployments with no pipeline configuration continue to operate identically to the current behaviour (full-pipeline mode) with zero regressions.
- **SC-005**: Operators can identify the active pipeline mode and the processing steps executed for any email within 5 seconds of viewing the dashboard.
