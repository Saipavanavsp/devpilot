from dataclasses import dataclass, field

import pytest

from devpilot.sandbox import CommandResult


@dataclass
class FakeSandbox:
    """Records commands and replays canned results instead of starting Docker."""

    results: list[CommandResult] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def run(self, command: str, timeout: int | None = None) -> CommandResult:
        self.commands.append(command)
        if self.results:
            return self.results.pop(0)
        return CommandResult(exit_code=0, output="")


@pytest.fixture
def fake_sandbox() -> FakeSandbox:
    return FakeSandbox()
