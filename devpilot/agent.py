from dataclasses import dataclass
from typing import Any

from devpilot.config import Settings
from devpilot.providers import ModelTurn, Provider, ToolCall, ToolOutput
from devpilot.runlog import RunLog
from devpilot.sandbox import WORKDIR
from devpilot.tools import TOOL_SPECS, ToolExecutor, ToolResult
from devpilot.usage import Usage

SYSTEM_PROMPT = f"""You are DevPilot, an autonomous software engineer. The repository is mounted \
at {WORKDIR} and you change it only through your tools.

How to work:
- Explore the relevant code before changing it, and read a file before editing it.
- When several tool calls don't depend on each other (for example reading three files), make \
them all in the same response.
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
MALFORMED = "Your last tool call could not be parsed. Call the tool again with valid arguments."
TRUNCATED = (
    "Your response was cut off before this tool call was complete. "
    "Retry with smaller steps, e.g. write large files in parts."
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


class Agent:
    def __init__(
        self,
        provider: Provider,
        settings: Settings,
        tools: ToolExecutor,
        log: RunLog,
        usage: Usage | None = None,
    ) -> None:
        self.provider = provider
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
        conversation = self.provider.start(
            self.system_prompt,
            TOOL_SPECS,
            f"<task>\n{task}\n</task>\n\n<plan>\n{plan_text}\n</plan>",
        )
        nudged = False

        for step in range(1, self.settings.max_steps + 1):
            if self._over_budget():
                return self._result("budget_exceeded", "Stopped: run budget exhausted.", step - 1)

            turn = conversation.send()
            self.usage.add(turn.usage)

            if turn.stop == "refusal":
                self._log_step(step, turn, [])
                return self._result("refused", "The model declined to continue.", step)

            if not turn.tool_calls:
                self._log_step(step, turn, [])
                if turn.stop == "malformed":
                    conversation.add_user_message(MALFORMED)
                    continue
                if nudged:
                    return self._result("finished", turn.text or "Agent stopped.", step)
                nudged = True
                conversation.add_user_message(NUDGE)
                continue

            outputs = self._run_tools(turn)
            conversation.add_tool_results(outputs)
            self._log_step(step, turn, outputs)

            finished = next(
                (o for o in outputs if o.call.name == "finish" and not o.is_error), None
            )
            if finished is not None:
                return self._result("finished", finished.content, step)

        return self._result(
            "max_steps", f"Stopped after {self.settings.max_steps} steps.", self.settings.max_steps
        )

    def _run_tools(self, turn: ModelTurn) -> list[ToolOutput]:
        outputs: list[ToolOutput] = []
        for call in turn.tool_calls:
            if turn.stop == "max_tokens" and call is turn.tool_calls[-1]:
                # The response hit the output limit, so the last tool input may be cut off.
                result = ToolResult(TRUNCATED, is_error=True)
            else:
                result = self.tools.execute(call.name, call.arguments)
            outputs.append(ToolOutput(call=call, content=result.content, is_error=result.is_error))
        return outputs

    def _log_step(self, step: int, turn: ModelTurn, outputs: list[ToolOutput]) -> None:
        self.log.add_step(
            {
                "step": step,
                "thought": _clip(turn.text, LOGGED_TEXT_LIMIT),
                "tool_calls": [self._loggable_call(o.call, o) for o in outputs],
                "tokens": turn.usage.to_dict(),
                "cost_so_far_usd": round(self.cost, 4),
            }
        )

    @staticmethod
    def _loggable_call(call: ToolCall, output: ToolOutput) -> dict[str, Any]:
        return {
            "tool": call.name,
            "input": {key: _clip(value) for key, value in call.arguments.items()},
            "result": _clip(output.content, LOGGED_TEXT_LIMIT),
            "is_error": output.is_error,
        }

    def _result(self, status: str, summary: str, steps: int) -> AgentResult:
        return AgentResult(
            status=status,
            summary=summary,
            steps=steps,
            usage=self.usage,
            cost_usd=self.cost,
            tests_passed=self.tools.last_tests_passed,
        )
