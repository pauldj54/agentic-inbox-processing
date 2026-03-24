# Research: Triage Consumer Client

**Feature**: 005-triage-consumer | **Date**: 2025-07-17

## R-001: Service Bus Consumer Pattern

**Decision**: Use `receiver.receive_messages(max_message_count=1, max_wait_time=30)` in a continuous `while True` loop with `ServiceBusClient` context manager.

**Rationale**: This is the established pattern in the codebase. Both `queue_tools.py` (line 105) and `peek_queue.py` use the same approach. Single-message processing (`max_message_count=1`) keeps the consumer simple and avoids partial-batch failure scenarios.

**Alternatives considered**:
- `receive_deferred_messages()` — not applicable; deferred messages are for sequence-number-based retrieval, not standard consumption
- Async receiver (`aio` variant) — unnecessary complexity for a single-threaded CLI tool with no concurrency requirements
- Azure Functions Service Bus trigger — would require deployment infrastructure; this is a local dev tool

## R-002: Message Acknowledgment Strategy

**Decision**: Always complete (acknowledge) messages after processing, regardless of API call success or failure.

**Rationale**: The consumer is a forwarding tool, not a transactional processor. If the external API fails, the message has already been displayed to the operator and the failure logged. Leaving messages on the queue would cause infinite reprocessing loops with no benefit, since the API failure likely persists. This aligns with FR-006 and prevents queue poisoning from transient API outages.

**Alternatives considered**:
- Abandon on API failure (return to queue for retry) — rejected because API failures are typically persistent (wrong endpoint, auth issues) and would cause message churn
- Dead-letter on API failure — rejected because dead-lettering is for message-level problems, not downstream system failures. The codebase's dead-letter pattern (`queue_tools.py` line 390) is used for classification routing failures, not API call failures
- Complete on success, abandon on failure with max retry count — over-engineered for a dev/ops tool; appropriate for production pipelines

## R-003: HTTP Client Choice (requests vs aiohttp)

**Decision**: Use `requests` library for outbound API calls.

**Rationale**: The consumer is synchronous and single-threaded. `requests` is the standard Python HTTP client for synchronous use. `aiohttp` (already in project) is async-only and would require wrapping in `asyncio.run()` or converting the entire consumer to async, adding complexity with no performance benefit for a one-message-at-a-time tool.

**Alternatives considered**:
- `aiohttp` — already in project but async-only. Used in `graph_tools.py` and `link_download_tool.py` for their async contexts. Would require unnecessary async machinery here.
- `urllib.request` (stdlib) — no external dependency, but lacks convenient JSON handling, timeout support, and error classification. `requests` provides these with minimal footprint.
- `httpx` — modern alternative to `requests` with async support, but adding a new dependency when `requests` suffices violates the minimal-dependency constitution principle.

## R-004: Configuration Strategy

**Decision**: Read all config from environment variables via `.env01` file using `python-dotenv`, with sensible defaults for optional values.

**Rationale**: Follows the established project pattern. All existing tools (`email_classifier_agent.py`, `queue_tools.py`, `graph_tools.py`) load config from `.env01`. Environment variables enable easy override without code changes.

**Alternatives considered**:
- YAML/TOML config file — adds file parsing dependency and a new config convention. Project already standardized on .env files.
- CLI arguments — adds argument parsing complexity. Environment variables cover the same need with less code.

## R-005: Dependency Addition

**Decision**: Add `requests>=2.31.0` to `requirements.txt`.

**Rationale**: Required for HTTP API calls. Not currently in requirements.txt despite being used by the triage consumer. The `requests` library is the most widely used Python HTTP client, well-maintained, and has a minimal transitive dependency footprint.

**Note**: `requests` is already installed in the virtual environment as a transitive dependency of other packages, but must be explicitly declared per Python packaging best practices.
