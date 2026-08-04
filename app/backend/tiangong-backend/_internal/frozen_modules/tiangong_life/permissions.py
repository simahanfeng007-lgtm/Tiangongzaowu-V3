"""Read-only model meta actions available without execution authority."""
READ_ACTIONS = frozenset({"skill.get", "skill.read", "skill.list", "skill.route", "skill.step.check", "skill.progress.report", "file.read", "system.health"})

def is_read_action(action: str) -> bool:
    return action in READ_ACTIONS
