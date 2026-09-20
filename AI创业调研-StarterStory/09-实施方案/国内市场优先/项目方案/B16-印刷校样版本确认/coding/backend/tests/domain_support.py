"""Test-only code delivery: exercises the real challenge/verify protocol."""
from identity.code_providers import DevelopmentCodeProvider


class TestCodeProvider(DevelopmentCodeProvider):
    __test__ = False
    messages = []
