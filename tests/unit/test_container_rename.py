"""T031: Verify container rename from 'emails' to 'intake-records'."""

from src.agents.tools.cosmos_tools import CosmosDBTools


class TestContainerRename:
    """Verify CONTAINER_INTAKE_RECORDS constant and old constant removal."""

    def test_container_intake_records_equals_intake_records(self):
        assert CosmosDBTools.CONTAINER_INTAKE_RECORDS == "intake-records"

    def test_old_container_emails_constant_does_not_exist(self):
        assert not hasattr(CosmosDBTools, "CONTAINER_EMAILS")
