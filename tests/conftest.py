"""Shared pytest configuration: the `slow` marker (a 50 MB synthetic-log timing
test) is skipped when `CI` is set, so the gate runs locally and on the bench
box but never stretches a CI job."""

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: long-running timing gate (skipped when CI is set)")


def pytest_collection_modifyitems(config, items):
    if not os.environ.get("CI"):
        return
    skip = pytest.mark.skip(reason="slow test skipped on CI")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)
