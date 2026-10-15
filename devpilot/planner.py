import json

import anthropic

from devpilot.config import Settings
from devpilot.llm import create_message
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


def make_plan(
    client: anthropic.Anthropic, settings: Settings, task: str, file_listing: str
) -> tuple[list[str], Usage]:
    response = create_message(
        client,
        settings,
        max_tokens=8000,
        system=PLANNER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"<task>\n{task}\n</task>\n\n<files>\n{file_listing}\n</files>",
            }
        ],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}},
    )
    usage = Usage.from_api(response.usage)
    if response.stop_reason == "refusal":
        raise PlanningError("The model declined to plan this task.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        steps = [step.strip() for step in json.loads(text)["steps"] if step.strip()]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PlanningError(f"Planner returned an unreadable plan: {text[:200]!r}") from exc
    if not steps:
        raise PlanningError("Planner returned an empty plan.")
    return steps, usage
