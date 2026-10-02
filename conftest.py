"""Pytest shim: the test classes use unittest-style assertions
(`self.assertIsNotNone`) without inheriting from unittest.TestCase.
Provide the assertion methods they rely on without modifying test files.
"""


def _assert_is_not_none(self, obj, msg=None):
    assert obj is not None, msg or "unexpectedly None"


def pytest_collection_modifyitems(items):
    for item in items:
        cls = getattr(item, "cls", None)
        if cls is not None and not hasattr(cls, "assertIsNotNone"):
            cls.assertIsNotNone = _assert_is_not_none
