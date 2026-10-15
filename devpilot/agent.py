from dataclasses import dataclass
from typing import Any

import anthropic

from devpilot.config import Settings
from devpilot.llm import create_message, response_text
from devpilot.runlog import RunLog
from devpilot.sandbox import WORKDIR
from devpilot.tools import TOOL_SPECS, ToolExecutor, ToolResult
from devpilot.usage import Usage

SYSTEM_PROMPT = f"""You are DevPilot, an autonomous software engineer. The repository is mounted \
at {WORKDIR} and you change it only through your tools.

How to work:
- Explore the relevant code before changing it, and read a file before editing it.
- Make focused changes that solve the task; don't refactor unrelated code.
- Commands run in an isolated Linux container with Python 3.12 and pytest. {{network_note}}
- Verify your work with run_tests. If tests fail, read the output, fix the cause and rerun them.
- Add or update tests for behaviour you change when the project has a test suite. Don't weaken \
or delete existing tests to make them pass.
- When the work is done and the tests pass, call finish with a short summary of what changed \
and why. Don't call finish while tests are failing unless you cannot fix them, and then say so \
in the summary."""

NETWORK_ON = "Network access is available for installing packages."
NETWORK_OFF = "There is no network access, so you cannot install packages."

NUDGE = (
    "You stopped without calling a tool. If the task is complete and the tests pass, "
    "call finish with a summary. Otherwise continue working."
)

LOGGED_TEXT_LIMIT = 1_000


@dataclass
class AgentResult:
    status: str
    summary: str
    steps: int
    usage: Usage
    cost_usd: float
    tests_passed: bool | None = None


def _clip(value: Any, limit: int = 300) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}... [{len(value)} chars]"
    return value


def _loggable_input(arguments: dict[str, Any]) -> dict[str, Any]:
    return {key: _clip(value) for key, value in arguments.items()}


class Agent:
    def __init__(
        self,
        client: anthropic.Anthropic,
        settings: Settings,
        tools: ToolExecutor,
        log: RunLog,
        usage: Usage | None = None,
    ) -> None:
        self.client = client
        self.settings = settings
        self.tools = tools
        self.log = log
        self.usage = usage or Usage()
        self.system_prompt = SYSTEM_PROMPT.format(
            network_note=NETWORK_ON if settings.sandbox_network else NETWORK_OFF
        )

    @property
    def cost(self) -> float:
        return self.usage.cost(self.settings.price)

    def _over_budget(self) -> bool:
        return (
            self.cost >= self.settings.max_cost_usd
            or self.usage.total_tokens >= self.settings.max_tokens
        )

    def run(self, task: str, plan: list[str]) -> AgentResult:
        plan_text = "\n".join(f"{i}. {step}" for i, step in enumerate(plan, start=1))
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"<task>\n{task}\n</task>\n\n<plan>\n{plan_text}\n</plan>"}
        ]
        nudged = False

        for step in range(1, self.settings.max_steps + 1):
            if self._over_budget():
                return self._result("budget_exceeded", "Stopped: run budget exhausted.", step - 1)

            response = self._call_model(messages)
            step_usage = Usage.from_api(response.usage)
            self.usage.add(step_usage)
            messages.append({"role": "assistant", "content": response.content})
            thought = response_text(response.content)

            if response.stop_reason == "refusal":
                self._log_step(step, thought, [], step_usage)
                return self._result("refused", "The model declined to continue.", step)

            tool_calls = [block for block in response.content if block.type == "tool_use"]
            if not tool_calls:
                self._log_step(step, thought, [], step_usage)
                if nudged:
                    return self._result("finished", thought or "Agent stopped.", step)
                nudged = True
                messages.append({"role": "user", "content": NUDGE})
                continue

            truncated = response.stop_reason == "max_tokens"
            results, logged_calls, summary = self._run_tools(tool_calls, truncated)
            messages.append({"role": "user", "content": results})
            self._log_step(step, thought, logged_calls, step_usage)

            if summary is not None:
                return self._result("finished", summary, step)

        return self._result(
            "max_steps", f"Stopped after {self.settings.max_steps} steps.", self.settings.max_steps
        )

    def _call_model(self, messages: list[dict[str, Any]]) -> Any:
        return create_message(
            self.client,
            self.settings,
            progress_updates=True,
            max_tokens=16_000,
            system=self.system_prompt,
            tools=TOOL_SPECS,
            messages=messages,
            cache_control={"type": "ephemeral"},
            output_config={"effort": self.settings.effort},
        )

    def _run_tools(
        self, tool_calls: list[Any], truncated: bool
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
        results: list[dict[str, Any]] = []
        logged: list[dict[str, Any]] = []
        summary: str | None = None
        for call in tool_calls:
            if truncated and call is tool_calls[-1]:
                # The response hit max_tokens, so the last tool input may be cut off.
                result = ToolResult(
                    "Your response was cut off before this tool call was complete. "
                    "Retry with smaller steps, e.g. write large files in parts.",
                    is_error=True,
                )
            else:
                result = self.tools.execute(call.name, dict(call.input))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
            )
            logged.append(
                {
                    "tool": call.name,
                    "input": _loggable_input(dict(call.input)),
                    "result": _clip(result.content, LOGGED_TEXT_LIMIT),
                    "is_error": result.is_error,
                }
            )
            if result.finished:
                summary = result.content
        return results, logged, summary

    def _log_step(
        self, step: int, thought: str, calls: list[dict[str, Any]], step_usage: Usage
    ) -> None:
        self.log.add_step(
            {
                "step": step,
                "thought": _clip(thought, LOGGED_TEXT_LIMIT),
                "tool_calls": calls,
                "tokens": step_usage.to_dict(),
                "cost_so_far_usd": round(self.cost, 4),
            }
        )

    def _result(self, status: str, summary: str, steps: int) -> AgentResult:
        return AgentResult(
            status=status,
            summary=summary,
            steps=steps,
            usage=self.usage,
            cost_usd=self.cost,
            tests_passed=self.tools.last_tests_passed,
        )
