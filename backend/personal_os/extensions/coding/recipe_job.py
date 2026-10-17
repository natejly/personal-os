"""The `coding_recipe` job kind: a saved recipe runs on a schedule, pinned to the version the job was created with.

A job of this kind holds its settings as JSON in its prompt: {"recipe", "version", "inputs", "cron"}. Creating it is the user's
approval of exactly that version with those inputs; editing the recipe makes a new version, which the job ignores until the
user re-pins it. When the cron slot comes the trigger starts a recipe run itself (a coding session and its verify command),
not a chat run, and says so through `job.fired`; `job.done` follows with the run's outcome. A run that finds every session
slot taken waits for one (recipes.Recipes.run).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ... import autoreview, shell
from ...jobs import next_fire, valid_cron
from .recipes import RecipeError
from .sessions import CodingError

if TYPE_CHECKING:
    from ...jobs import Jobs
    from .extension import Coding

log = logging.getLogger(__name__)


def config(job: dict[str, Any]) -> dict[str, Any]:
    """The job's recipe settings, or a RecipeError saying what is wrong with them."""
    try:
        cfg = json.loads(job.get("prompt") or "")
    except ValueError as e:
        raise RecipeError("A coding_recipe job's prompt is JSON: {\"recipe\": name, \"version\": n, \"inputs\": {...}, \"cron\": \"0 3 * * *\"}") from e
    if not isinstance(cfg, dict) or not isinstance(cfg.get("recipe"), str):
        raise RecipeError("The prompt must name a \"recipe\".")
    if isinstance(cfg.get("version"), bool) or not isinstance(cfg.get("version"), int):
        raise RecipeError("The prompt must pin a \"version\" (a whole number): the job keeps running it until you re-pin it.")
    if not valid_cron(str(cfg.get("cron") or "")):
        raise RecipeError("The prompt's \"cron\" must be a cron expression with five fields.")
    if not isinstance(cfg.get("inputs", {}), dict):
        raise RecipeError("\"inputs\" must be an object.")
    return cfg


class RecipeTrigger:
    hint = "'coding_recipe' to run a saved coding recipe on a schedule (its settings are JSON in the prompt)"

    def __init__(self, coding: Coding) -> None:
        self.coding = coding

    def check(self, job: dict[str, Any]) -> str | None:
        if job.get("cron"):
            return "A coding_recipe job keeps its cron expression in its prompt; leave this one empty"
        try:
            cfg = config(job)
            self.coding.recipes.preview(cfg["recipe"], cfg.get("inputs"), cfg["version"])  # the same checks a run makes
        except RecipeError as e:
            return str(e)
        return None

    async def poll(self, jobs: Jobs, job: dict[str, Any], at: float) -> tuple[None, float]:
        """One look: when the slot after the last run (or after the job was made) has come, start a run and book the next.
        Always returns no fire record, because the run is not a chat turn."""
        cfg = config(job)
        due = next_fire(cfg["cron"], job["timezone"], job.get("last_fired_at") or job["created_at"])
        if due > at:
            return None, due
        mode = autoreview.mode_of(self.coding.tb.settings())
        ctx = {"conversation_id": None, "run_id": None, "settings": self.coding.tb.settings(),  # Allow everything does not lift an unattended run to bypass
               "permission_mode": "auto" if mode == "allow_all" else mode, "tainted": False, "taint_sources": []}
        nxt = next_fire(cfg["cron"], job["timezone"], at)
        try:
            out = await self.coding.recipes.run(cfg["recipe"], cfg.get("inputs"), "job", ctx, version=cfg["version"], job_id=job["id"])
        except (RecipeError, CodingError, shell.ShellError) as e:  # this slot is spent; the next one tries again
            log.info("coding_recipe job %s could not start its run: %s", job["name"], e)
            jobs.record_skip(job["id"], "recipe_not_started", at, due)
            return None, nxt
        jobs.mark_fired(job["id"], fired_at=at, due_at=due, next_due_at=nxt, run_id=out["run_id"])
        self.coding.api.bus.fire("job.fired", {"job_id": job["id"], "kind": "coding_recipe", "trigger": "clock"})
        return None, nxt

    def prompt_block(self, fire: dict[str, Any]) -> str:
        return ""
