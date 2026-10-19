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

from ... import shell
from ...jobs import next_fire, valid_cron
from .recipes import RecipeError, failure_text, unattended_mode
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

    async def start_run(self, job: dict[str, Any], origin: str = "palette") -> str:
        """Start the job's recipe run now and return its id. A clock slot starts it as "job"; Run now is the user's own
        start ("palette"), which fails at once when no session is free and never counts toward the job's failure streak."""
        cfg = config(job)
        settings = self.coding.tb.settings()
        ctx = {"conversation_id": None, "run_id": None, "settings": settings, "permission_mode": unattended_mode(settings),
               "tainted": False, "taint_sources": []}
        try:
            out = await self.coding.recipes.run(cfg["recipe"], cfg.get("inputs"), origin, ctx, version=cfg["version"], job_id=job["id"])
        except shell.ShellError as e:
            raise RecipeError(str(e)) from e
        return out["run_id"]

    async def poll(self, jobs: Jobs, job: dict[str, Any], at: float) -> tuple[None, float]:
        """One look: when the slot after the last run (or after the job was last saved, which re-arms it) has come, start a
        run and book the next. A slot that finds the job's previous run still going is skipped. Always returns no fire
        record, because the run is not a chat turn."""
        cfg = config(job)
        due = next_fire(cfg["cron"], job["timezone"], max(job.get("last_fired_at") or 0, job["updated_at"]))
        if due > at:
            return None, due
        nxt = next_fire(cfg["cron"], job["timezone"], at)
        if self.coding.recipes.has_live_run(job["id"]):
            jobs.record_skip(job["id"], "previous run still running", at, due)
            return None, nxt
        try:
            run_id = await self.start_run(job, "job")
        except (RecipeError, CodingError) as e:  # this slot is spent; the next one tries again
            log.info("coding_recipe job %s could not start its run: %s", job["name"], e)
            jobs.record_skip(job["id"], "recipe_not_started", at, due)
            return None, nxt
        jobs.mark_fired(job["id"], fired_at=at, due_at=due, next_due_at=nxt, run_id=run_id,
                        error=failure_text(self.coding.recipes.get_run(run_id)))  # a run that failed to start has already settled
        self.coding.api.bus.fire("job.fired", {"job_id": job["id"], "kind": "coding_recipe", "trigger": "clock"})
        return None, nxt

    def prompt_block(self, fire: dict[str, Any]) -> str:
        return ""
