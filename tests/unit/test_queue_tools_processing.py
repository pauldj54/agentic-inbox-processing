import pytest

from src.agents.tools.queue_tools import QueueTools


class FakeCredential:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeMessage:
    sequence_number = 42
    message_id = "message-42"
    enqueued_time_utc = None

    def __str__(self):
        return '{"emailId":"email-42","hasAttachments":false}'


class FakeReceiver:
    def __init__(self, events):
        self.events = events
        self.message = FakeMessage()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def receive_messages(self, max_message_count, max_wait_time):
        self.events.append("receive")
        return [self.message]

    async def complete_message(self, message):
        self.events.append("complete")

    async def abandon_message(self, message):
        self.events.append("abandon")


class FakeClient:
    def __init__(self, *, fully_qualified_namespace, credential, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get_queue_receiver(self, queue_name, max_wait_time):
        return FakeReceiver(self.events)


class FakeLockRenewer:
    def __init__(self, *args, **kwargs):
        self.events = kwargs.pop("events", None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def register(self, receiver, message, max_lock_renewal_duration):
        return None


@pytest.mark.asyncio
async def test_process_email_from_intake_completes_after_processor_success(monkeypatch):
    events = []

    def fake_client_factory(*args, **kwargs):
        return FakeClient(*args, **kwargs, events=events)

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", lambda: FakeCredential())
    monkeypatch.setattr("src.agents.tools.queue_tools.AsyncServiceBusClient", fake_client_factory)
    monkeypatch.setattr("azure.servicebus.aio.AutoLockRenewer", FakeLockRenewer)

    async def processor(email_message):
        events.append("processor")
        assert email_message["body"]["emailId"] == "email-42"
        return {"ok": True}

    result = await QueueTools(namespace="fake-ns").process_email_from_intake(processor)

    assert result == {"ok": True}
    assert events == ["receive", "processor", "complete"]


@pytest.mark.asyncio
async def test_process_email_from_intake_abandons_after_processor_failure(monkeypatch):
    events = []

    def fake_client_factory(*args, **kwargs):
        return FakeClient(*args, **kwargs, events=events)

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", lambda: FakeCredential())
    monkeypatch.setattr("src.agents.tools.queue_tools.AsyncServiceBusClient", fake_client_factory)
    monkeypatch.setattr("azure.servicebus.aio.AutoLockRenewer", FakeLockRenewer)

    async def processor(email_message):
        events.append("processor")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await QueueTools(namespace="fake-ns").process_email_from_intake(processor)

    assert events == ["receive", "processor", "abandon"]
