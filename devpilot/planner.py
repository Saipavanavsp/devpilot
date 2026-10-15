import json

from devpilot.providers import Provider, ProviderError
from devpilot.usage import Usage

PLANNER_SYSTEM = (
    "You are the planning stage of an autonomous coding agent. Given a task and the files in "
    "the repository, write a short numbered plan of 3 to 7 concrete steps the agent will follow. "
    "Each step is one sentence and names the files or commands involved when you can tell. "
    "The last step is always to run the tests and confirm they pass."
)

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
    "required": ["steps"],
    "additionalProperties": False,
}


class PlanningError(Exception):
    pass


def make_plan(provider: Provider, task: str, file_listing: str) -> tuple[list[str], Usage]:
    prompt = f"<task>\n{task}\n</task>\n\n<files>\n{file_listing}\n</files>"
    try:
        text, usage = provider.complete_json(PLANNER_SYSTEM, prompt, PLAN_SCHEMA)
    except ProviderError as exc:
        raise PlanningError(str(exc)) from exc
    try:
        steps = [step.strip() for step in json.loads(text)["steps"] if step.strip()]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise PlanningError(f"Planner returned an unreadable plan: {text[:200]!r}") from exc
    if not steps:
        raise PlanningError("Planner returned an empty plan.")
    return steps, usage
