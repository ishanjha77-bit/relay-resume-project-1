import pytest
from support import LOGS, METRICS, FakeToolbox


@pytest.fixture
def toolbox() -> FakeToolbox:
    return FakeToolbox({"logs__error_summary": LOGS, "metrics__service_health": METRICS})
