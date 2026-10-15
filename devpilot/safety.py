import re

# Commands run inside a locked-down container, so this list is a second line of defence:
# it stops the agent from attempting obviously destructive or escaping actions at all.
BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\brm\s+-[a-z]*[rf][a-z]*\s+(/|~|\$HOME|\.\.)(\s|/|$)"),
        "recursive delete outside the workspace",
    ),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "formatting a filesystem"),
    (re.compile(r"\bdd\s+.*\bof=/dev/"), "writing to a raw device"),
    (re.compile(r">\s*/dev/(sd|nvme|hd)"), "writing to a raw device"),
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}"), "fork bomb"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "power management"),
    (re.compile(r"\bsudo\b|\bsu\s+-|\bsu\s+root\b"), "privilege escalation"),
    (
        re.compile(r"\bchmod\s+(-r\s+)?[0-7]*777\s+/(\s|$)"),
        "opening permissions on the root filesystem",
    ),
    (re.compile(r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b"), "piping a download into a shell"),
    (re.compile(r"\bnc\b.*\s-[a-z]*e\b"), "reverse shell"),
    (re.compile(r"\bgit\s+push\b"), "pushing is handled by DevPilot, not the agent"),
    (re.compile(r"\bdocker\b"), "nested container control"),
    (re.compile(r"\bmount\b"), "mounting filesystems"),
    (re.compile(r"\bkill\s+-9\s+-1\b"), "killing every process"),
]


def check_command(command: str) -> str | None:
    """Return the reason a command is blocked, or None if it may run."""
    lowered = command.lower()
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(lowered):
            return reason
    return None
