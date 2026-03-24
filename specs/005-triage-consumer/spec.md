# Feature Specification: Triage Consumer Client

**Feature Branch**: `005-triage-consumer`  
**Created**: 2025-07-17  
**Status**: Draft  
**Input**: User description: "Create a Python client that listens to the triage-complete queue, processes incoming messages, simulates an API call for document processing, and displays relevant document information in the terminal"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Monitor Triaged Documents in Real Time (Priority: P1)

As a developer or operator, I want to run a consumer client that continuously listens to the triage-complete queue so that I can see incoming triaged documents as they arrive, including their metadata, attachments, and classification details.

**Why this priority**: This is the core value of the feature — without real-time queue consumption and display, there is no utility. It enables operators to observe the triage pipeline output without needing to query Cosmos or inspect logs.

**Independent Test**: Can be fully tested by sending a sample message to the triage-complete queue and verifying the consumer displays document ID, intake source, subject, sender, attachments, relevance score, category, and routing information in a readable format.

**Acceptance Scenarios**:

1. **Given** the consumer is running and connected to the queue, **When** a triaged email message arrives on the triage-complete queue, **Then** the consumer displays the document ID, subject, sender, intake source, attachment count, attachment names/links, relevance confidence score, initial category, pipeline mode, status, and routing path.
2. **Given** the consumer is running, **When** a triaged SFTP file message arrives, **Then** the consumer displays the original filename, file type, and blob path in addition to the standard fields.
3. **Given** no messages are in the queue, **When** the consumer is waiting, **Then** it continues polling without errors and resumes processing when a new message arrives.
4. **Given** the consumer is running, **When** the user presses Ctrl+C, **Then** the consumer shuts down gracefully, closing the Service Bus connection.

---

### User Story 2 - Forward Triaged Documents to an External API (Priority: P2)

As a developer, I want each consumed message to be transformed into an API request payload and submitted to a configurable document processing endpoint so that I can integrate triage output with downstream processing systems.

**Why this priority**: Bridges the gap between the triage pipeline and external document processing. Without this, triaged documents would need manual intervention to reach the next stage.

**Independent Test**: Can be tested by running the consumer with a mock or real API endpoint, sending a message to the queue, and verifying the outbound request payload contains the correct document SAS URLs, project name, analysis name, data model name, language, and metadata.

**Acceptance Scenarios**:

1. **Given** a triaged email message with attachments, **When** the consumer processes it, **Then** it constructs an API request containing a document entry for each attachment with a SAS URL and document name.
2. **Given** a triaged message with a subject containing a fund name, **When** the API request is built, **Then** the project name is derived from the fund name in the subject line.
3. **Given** the API endpoint returns a success response, **When** the consumer completes the call, **Then** the message is acknowledged and removed from the queue.
4. **Given** the API endpoint is unreachable or returns an error, **When** the consumer attempts the call, **Then** the failure is logged and the message is still acknowledged to prevent infinite reprocessing.

---

### User Story 3 - Send Test Messages for Development (Priority: P3)

As a developer, I want a utility to send realistic sample messages to the triage-complete queue so that I can test the consumer without waiting for real emails or SFTP files to flow through the pipeline.

**Why this priority**: Essential for local development and testing but not part of the core runtime consumer. Without this, testing depends on the full upstream pipeline being active.

**Independent Test**: Can be tested by running the utility, choosing a message type (email or SFTP), and verifying the message appears in the triage-complete queue with correct structure.

**Acceptance Scenarios**:

1. **Given** the test utility is run, **When** the user selects "email" message type, **Then** a realistic triage message is sent with email-specific fields (subject, sender, attachments with blob URLs).
2. **Given** the test utility is run, **When** the user selects "SFTP" message type, **Then** a realistic triage message is sent with SFTP-specific fields (originalFilename, fileType, blobPath).
3. **Given** the queue is accessible, **When** a test message is sent, **Then** the consumer (if running) receives and displays it within the normal polling interval.

---

### Edge Cases

- What happens when a message arrives with malformed JSON? The consumer must log the parse error and acknowledge the message to avoid queue poisoning.
- What happens when a message has no attachments? The consumer must display the document info without attachment details and send an API request with an empty documents list.
- What happens when the Service Bus namespace is unreachable at startup? The consumer must display a clear error message and exit rather than silently failing.
- What happens when a message has attachment paths as plain strings instead of dictionaries? The consumer must handle both formats and extract usable URLs from either.
- What happens when the consumer loses the Service Bus connection mid-operation? The consumer must log the error and attempt to reconnect on the next iteration of the loop.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST continuously listen to the triage-complete queue and process messages as they arrive.
- **FR-002**: System MUST display a formatted summary of each triaged document in the terminal, including document ID, intake source, subject or filename, sender, timestamp, attachment details, relevance score, category, and routing information.
- **FR-003**: System MUST differentiate between email-sourced and SFTP-sourced messages, displaying source-specific fields (blob path, original filename for SFTP; subject, sender for email).
- **FR-004**: System MUST transform each queue message into an API request payload containing document SAS URLs, project name, analysis name, data model name, language, and originating metadata.
- **FR-005**: System MUST submit the API request to a configurable external endpoint and log the outcome (success or failure).
- **FR-006**: System MUST acknowledge (complete) every processed message to remove it from the queue, regardless of API call success or failure.
- **FR-007**: System MUST read all configuration (namespace, queue name, API endpoint, default project name, data model, language) from environment variables with sensible defaults.
- **FR-008**: System MUST provide a separate test utility for sending realistic sample messages (both email and SFTP types) to the triage-complete queue.
- **FR-009**: System MUST shut down gracefully on user interrupt (Ctrl+C), closing the Service Bus connection.
- **FR-010**: System MUST handle malformed messages by logging issues and acknowledging the message to prevent queue poisoning.

### Constitution Alignment Requirements *(mandatory)*

- **CAR-001 (Code Simplicity)**: Solution MUST favor clear, maintainable code and avoid unnecessary
  abstractions. The consumer is a single-file script with straightforward functions — no class hierarchies or plugin systems.
- **CAR-002 (UX Simplicity)**: User-facing behavior MUST be minimal and focused on essential actions.
  The consumer is a run-and-watch tool with no interactive controls beyond Ctrl+C to stop.
- **CAR-003 (Responsive Design)**: Not applicable — this is a terminal-based tool with no UI.
- **CAR-004 (Dependencies)**: New dependencies MUST be explicitly justified; existing or standard
  capabilities are preferred when sufficient. Uses `azure-servicebus` and `azure-identity` (already in project), plus `requests` for HTTP calls.
- **CAR-005 (Auth)**: Authentication MUST default to Microsoft Entra ID; non-Entra methods require
  explicit justification. Uses `DefaultAzureCredential` for Service Bus access — no shared keys.
- **CAR-006 (Azure/Microsoft SDKs)**: For Azure or Microsoft services, official Python SDKs MUST be
  used when available and stable for the scenario. Uses official `azure-servicebus` SDK.
- **CAR-007 (Testing Scope)**: Verification MUST be risk-based and focused; avoid over-extensive test
  suites for low-risk changes. The separate test message utility provides manual verification; unit tests should cover message parsing and API request building.
- **CAR-008 (Logging Discipline)**: Production behavior MUST rely on structured logging and avoid
  verbose print statements. Uses Python `logging` module for operational messages; formatted print output is intentional for the terminal display feature.

### Key Entities

- **Triage Message**: The core data structure consumed from the queue. Uses **camelCase** naming convention (set by the producer `email_classifier_agent.py`). Contains emailId, intakeSource, subject, sender, receivedAt, processedAt, hasAttachments, attachmentsCount, attachmentPaths, relevance (confidence, initialCategory, reasoning), pipelineMode, status, and routing (sourceQueue, targetQueue, routedAt). SFTP messages additionally include originalFilename, fileType, and blobPath. The `attachmentPaths` element objects are pass-through payloads from multiple upstream sources (Logic App, download processor, SFTP handler) with mixed conventions (`local_link`, `blobUrl`, `path`, `source`, `url`, `content_type`); the consumer handles all variants defensively.
- **API Request Payload**: The transformed output sent to the external endpoint. Uses **snake_case** naming convention (separate outbound boundary, follows Python/REST conventions). Contains documents (list of sas_url + document_name pairs), project_name, analysis_name, analysis_description, data_model_name, classifier_name, language, created_by, auto_extract, and _metadata (email_id, intake_source, processed_at).

## Clarifications

### Session 2026-03-23

- Q: What is the canonical naming convention for attributes in the triage-complete queue message payload? → A: **camelCase** for all queue message fields (top-level and nested objects). The `attachmentPaths` element objects are pass-through payloads from multiple upstream sources with mixed conventions — the consumer handles all variants. The outbound API request payload uses **snake_case** as a separate boundary convention.
- Q: How does the fund name heuristic behave when the subject contains multiple fund references? → A: The heuristic uses the **first match** found when scanning for "Fonds"/"Fund" keywords. If the match is ambiguous or no match is found, it falls back to `DEFAULT_PROJECT_NAME`.

## Assumptions

- The triage-complete queue already exists and receives messages from the existing email classifier agent.
- The external API endpoint is provided by a separate system; the consumer treats it as a configurable external dependency.
- Messages in the queue follow the established triage message schema produced by the email classifier agent.
- The consumer runs as a local development/operations tool, not as a deployed production service.
- Message acknowledgment happens regardless of API call outcome to prevent queue backup from transient API failures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can start the consumer and see triaged document details displayed within 30 seconds of a message arriving on the queue.
- **SC-002**: 100% of well-formed queue messages are processed and acknowledged without manual intervention.
- **SC-003**: Each processed message results in an outbound API request containing all document attachments from the original message.
- **SC-004**: The consumer runs continuously for 24+ hours without memory leaks, crashes, or connection drops under normal conditions. *(Verified via code review for proper resource cleanup patterns — no context managers leaking, no unbounded lists growing — rather than a soak test.)*
- **SC-005**: A developer can send a test message and see it processed end-to-end (queue → display → API call) within 60 seconds using the test utility.
- **SC-006**: The consumer handles malformed messages gracefully — zero unhandled exceptions from bad input.
