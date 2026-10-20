"""Example plugin: say "roll a dice" or "roll 2 dice". Copy this file to start your own."""
import random

from cozy.registry import Result, rule, tool


@tool("roll_dice", desc="roll one or more six-sided dice", args='{"count": 1}')
def roll_dice(ctx, count=1):
    count = max(1, min(int(count), 10))
    rolls = [random.randint(1, 6) for _ in range(count)]
    return Result(True, f"You rolled {', '.join(map(str, rolls))}" + (f" (total {sum(rolls)})." if count > 1 else "."))


rule(r"^roll\s+(?:a\s+)?(?:(?P<count>\d+)\s+)?dic?e$", "roll_dice", conv={"count": int}, priority=10)
