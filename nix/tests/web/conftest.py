import os

import pytest
from fake import FakeConnector
from fastapi.testclient import TestClient

import timekpr.web
from timekpr.web.app import create_app
from timekpr.web.bridge import Bridge

STATIC = os.path.join(os.path.dirname(timekpr.web.__file__), "static")
TOKEN = "secret"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def fake():
    return FakeConnector()


@pytest.fixture
def client(fake):
    return TestClient(create_app(Bridge(fake), static_dir=STATIC, token=TOKEN))
