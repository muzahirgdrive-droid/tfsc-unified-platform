import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_crm_core import workspace_fixture

@pytest.fixture(scope='session')
def workspace():
    yield from workspace_fixture()
