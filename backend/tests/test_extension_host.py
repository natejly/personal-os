"""The extension host: manifests, the hook bus (kinds, order, timeouts, failure semantics), disposal, loading, lifespan.

Run: python backend/tests/test_extension_host.py
"""
from __future__ import annotations

import asyncio
import functools
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from personal_os.db import Database  # noqa: E402
from personal_os.extensions.disposables import Disposable, Scope  # noqa: E402
from personal_os.extensions import hooks as hooks_mod  # noqa: E402
from personal_os.extensions.hooks import MUTE_AFTER, Ask, Deny, HookBus  # noqa: E402
from personal_os.extensions.host import ExtensionContext, ExtensionHost  # noqa: E402
from personal_os.extensions.manifest import ManifestError, Manifest, load_manifest  # noqa: E402

BASE = {"id": "demo", "name": "Demo", "version": "1.0.0", "apiVersion": 1}


def manifest(**over: Any) -> dict[str, Any]:
    return {**BASE, **over}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


CHAT = {"id": "c1", "project_id": None}


class Loose(unittest.TestCase):
    """The bus mechanics (order, timeouts, muting, fail-toward-ask) are exercised with stand-in payloads and returns, which
    the strict catalog check would refuse; the checks themselves are in test_hook_catalog."""

    def setUp(self) -> None:
        self.enterContext(mock.patch.object(hooks_mod, "STRICT", False))


class ManifestTests(unittest.TestCase):
    def test_minimal_manifest_gets_defaults(self) -> None:
        m = Manifest.from_dict(BASE)
        self.assertEqual((m.kind, m.enabled, m.activation, m.hooks), ("extension", True, ("startup",), {}))

    def test_full_manifest(self) -> None:
        m = Manifest.from_dict(manifest(
            enabled=False, requires={"services": ["mcp"]}, provides={"services": ["demo"]},
            contributes={"tools": [{"name": "demo_x"}], "routes": {"prefix": "/demo"}},
            hooks={"app.start": {}, "context.contribute": {"timeoutMs": 300, "priority": 10, "onFailure": "deny"}},
            permissions=["db:own", "service:mcp"], data={"tables": ["demo_t"], "migrations": "migrations/"}))
        self.assertEqual(m.contributes["tools"], [{"name": "demo_x"}])
        self.assertEqual(m.hooks["context.contribute"]["timeoutMs"], 300)
        self.assertEqual((m.requires_services, m.provides_services), (("mcp",), ("demo",)))

    def test_booleans_are_not_integers_or_enabled_flags(self) -> None:
        for bad in (manifest(hooks={"app.start": {"priority": True}}), manifest(hooks={"app.start": {"timeoutMs": True}}),
                    manifest(enabled=1), manifest(enabled=0)):
            with self.assertRaises(ManifestError):
                Manifest.from_dict(bad)

    def test_rejects(self) -> None:
        bad = {
            "unknown top-level key": manifest(tier="x"),
            "missing id": {k: v for k, v in BASE.items() if k != "id"},
            "bad id": manifest(id="Demo_1"),
            "bad version": manifest(version="1"),
            "newer api": manifest(apiVersion=2),
            "api zero": manifest(apiVersion=0),
            "bad kind": manifest(kind="plugin"),
            "bad enabled": manifest(enabled="yes"),
            "bad activation": manifest(activation=["onTuesday"]),
            "unknown contributes key": manifest(contributes={"toolz": []}),
            "tool without a name": manifest(contributes={"tools": [{}]}),
            "tool after not a name": manifest(contributes={"tools": [{"name": "demo_x", "after": 3}]}),
            "slots not a list": manifest(contributes={"slots": {"sidebar.row": {}}}),
            "slot entry not an object": manifest(contributes={"slots": {"sidebar.row": ["nav"]}}),
            "recap section without an id": manifest(contributes={"slots": {"recap.section": [{"order": 1}]}}),
            "canvas widget without an id": manifest(contributes={"slots": {"canvas.widget": [{"label": "Clock"}]}}),
            "canvas widget in another extension's namespace": manifest(contributes={"slots": {"canvas.widget": [{"id": "ext:other:clock"}]}}),
            "unknown hook": manifest(hooks={"tool.preee": {}}),
            "unknown hook option": manifest(hooks={"app.start": {"retries": 3}}),
            "onFailure allow": manifest(hooks={"tool.pre": {"onFailure": "allow"}}, permissions=["hooks:gate"]),
            "unknown permission": manifest(permissions=["root"]),
            "unknown requires key": manifest(requires={"packages": []}),
            "not an object": [],
        }
        for why, raw in bad.items():
            with self.subTest(why), self.assertRaises(ManifestError):
                Manifest.from_dict(raw)

    def test_load_manifest_reports_unreadable_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "extension.json"
            with self.assertRaises(ManifestError):
                load_manifest(p)
            p.write_text("{nope")
            with self.assertRaises(ManifestError):
                load_manifest(p)
            p.write_text(json.dumps(BASE))
            self.assertEqual(load_manifest(p).id, "demo")


class DisposableTests(unittest.TestCase):
    def test_dispose_is_idempotent(self) -> None:
        calls: list[int] = []
        d = Disposable(lambda: calls.append(1))
        d.dispose()
        d.dispose()
        self.assertEqual(calls, [1])

    def test_scope_disposes_newest_first_and_survives_a_failing_undo(self) -> None:
        order: list[str] = []
        s = Scope()
        s.add(Disposable(lambda: order.append("first")))
        s.add(Disposable(lambda: 1 / 0))
        s.add(Disposable(lambda: order.append("last")))
        with self.assertLogs("personal_os.extensions.disposables", "WARNING"):
            s.dispose()
        s.dispose()
        self.assertEqual(order, ["last", "first"])


def sub(bus: HookBus, hook: str, fn: Any, owner: str = "x", order: int = 0, **kw: Any) -> Disposable:
    return bus.subscribe(hook, fn, owner=owner, order=order, **kw)


class EmitTests(Loose):
    def test_empty_bus_emit_is_cheap(self) -> None:
        bus = HookBus()

        async def go() -> float:
            n = 2000
            t = time.perf_counter()
            for _ in range(n):
                await bus.emit("tool.post", {"name": "x"})
            return (time.perf_counter() - t) / n

        per_call = run(go())
        self.assertLess(per_call, 0.0001, f"empty-bus emit took {per_call * 1e6:.1f} us")

    def test_active_follows_subscription_and_disposal(self) -> None:
        bus = HookBus()
        self.assertFalse(bus.active("tool.pre"))
        d = sub(bus, "tool.pre", lambda p: None)
        self.assertTrue(bus.active("tool.pre"))
        d.dispose()
        self.assertFalse(bus.active("tool.pre"))
        with self.assertRaises(ValueError):
            bus.active("nope")

    def test_unknown_hook_is_an_error_everywhere(self) -> None:
        bus = HookBus()
        with self.assertRaises(ValueError):
            sub(bus, "nope", lambda p: None)
        for call in (bus.emit, bus.collect, bus.pipe, bus.gate):
            with self.assertRaises(ValueError):
                run(call("nope", 1))

    def test_handlers_run_concurrently_and_sync_ones_off_the_loop(self) -> None:
        bus = HookBus()
        seen: list[Any] = []
        main = threading.get_ident()

        async def slow(p: Any) -> None:
            await asyncio.sleep(0.2)
            seen.append("slow")

        sub(bus, "chat.created", slow)
        sub(bus, "chat.created", slow)
        sub(bus, "chat.created", lambda p: seen.append(threading.get_ident() != main))

        async def go() -> float:
            t = time.perf_counter()
            await bus.emit("chat.created", {"id": "c"})
            return time.perf_counter() - t

        self.assertLess(run(go()), 0.35)  # two 0.2 s handlers overlapped
        self.assertEqual(sorted(map(str, seen)), ["True", "slow", "slow"])

    def test_failure_and_timeout_are_contained(self) -> None:
        bus = HookBus()
        ok: list[int] = []

        async def hang(p: Any) -> None:
            await asyncio.sleep(5)

        sub(bus, "chat.created", lambda p: 1 / 0)
        sub(bus, "chat.created", hang, timeout_ms=50)
        sub(bus, "chat.created", lambda p: ok.append(1))
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            run(bus.emit("chat.created", None))
        self.assertEqual(ok, [1])

    def test_timeout_cancels_the_handler_task(self) -> None:
        bus = HookBus()
        cancelled: list[bool] = []

        async def hang(p: Any) -> None:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        sub(bus, "chat.created", hang, timeout_ms=30)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            run(bus.emit("chat.created", None))
        self.assertEqual(cancelled, [True])

    def test_a_handler_failing_repeatedly_is_muted_and_success_resets_the_count(self) -> None:
        bus = HookBus()
        calls: list[int] = []
        flaky = {"fail": True}

        def bad(p: Any) -> None:
            calls.append(1)
            if flaky["fail"]:
                raise RuntimeError("boom")

        sub(bus, "chat.created", bad)

        async def go() -> None:
            for _ in range(MUTE_AFTER - 1):
                await bus.emit("chat.created", None)
            flaky["fail"] = False
            await bus.emit("chat.created", None)  # the streak ends
            flaky["fail"] = True
            for _ in range(MUTE_AFTER + 3):
                await bus.emit("chat.created", None)

        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            run(go())
        self.assertEqual(len(calls), (MUTE_AFTER - 1) + 1 + MUTE_AFTER)


class CollectPipeTests(Loose):
    def test_collect_orders_by_priority_then_load_order_and_drops_none_and_failures(self) -> None:
        bus = HookBus()
        sub(bus, "context.contribute", lambda p: "late", order=5)
        sub(bus, "context.contribute", lambda p: "early", order=1)
        sub(bus, "context.contribute", lambda p: "first", priority=10, order=9)
        sub(bus, "context.contribute", lambda p: None)
        sub(bus, "context.contribute", lambda p: 1 / 0)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            out = run(bus.collect("context.contribute", {}))
        self.assertEqual(out, ["first", "early", "late"])

    def test_a_wrapped_async_handler_is_awaited(self) -> None:
        async def handler(tag: str, p: Any) -> str:
            return f"{tag}:{p}"

        bus = HookBus()
        sub(bus, "context.contribute", functools.partial(handler, "x"))
        self.assertEqual(run(bus.collect("context.contribute", 1)), ["x:1"])

    def test_collect_timeout_omits_only_the_slow_contribution(self) -> None:
        bus = HookBus()

        async def slow(p: Any) -> str:
            await asyncio.sleep(1)
            return "late"

        sub(bus, "context.contribute", slow, timeout_ms=30)
        sub(bus, "context.contribute", lambda p: "fast")
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertEqual(run(bus.collect("context.contribute", {})), ["fast"])

    def test_pipe_chains_in_order_and_skips_a_failing_or_silent_step(self) -> None:
        bus = HookBus()
        sub(bus, "model.select", lambda v: v + "b", priority=20)
        sub(bus, "model.select", lambda v: v + "a", priority=10)
        sub(bus, "model.select", lambda v: 1 / 0, priority=30)
        sub(bus, "model.select", lambda v: None, priority=40)
        sub(bus, "model.select", lambda v: v + "c", priority=50)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertEqual(run(bus.pipe("model.select", "x")), "xabc")

    def test_pipe_without_handlers_returns_the_value(self) -> None:
        self.assertEqual(run(HookBus().pipe("model.select", "x")), "x")


class GateTests(Loose):
    def test_no_handlers_or_no_opinion_is_none(self) -> None:
        bus = HookBus()
        self.assertIsNone(run(bus.gate("tool.pre", {})))
        sub(bus, "tool.pre", lambda p: None)
        self.assertIsNone(run(bus.gate("tool.pre", {})))

    def test_first_opinion_wins_and_later_handlers_do_not_run(self) -> None:
        bus = HookBus()
        later: list[int] = []
        sub(bus, "tool.pre", lambda p: Ask("check"), priority=10)
        sub(bus, "tool.pre", lambda p: later.append(1) or Deny("no"), priority=20)
        self.assertEqual(run(bus.gate("tool.pre", {})), Ask("check"))
        self.assertEqual(later, [])

    def test_an_exception_fails_toward_ask(self) -> None:
        bus = HookBus()
        sub(bus, "tool.pre", lambda p: 1 / 0, owner="guard")
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            out = run(bus.gate("tool.pre", {}))
        self.assertIsInstance(out, Ask)
        self.assertIn("guard", out.reason)
        self.assertIn("did not answer", out.reason)

    def test_a_failing_handler_does_not_hide_a_later_deny(self) -> None:
        bus = HookBus()
        sub(bus, "tool.pre", lambda p: 1 / 0, priority=10)
        sub(bus, "tool.pre", lambda p: Deny("no"), priority=60)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertEqual(run(bus.gate("tool.pre", {})), Deny("no"))

    def test_a_later_ask_does_not_soften_an_earlier_failure_to_deny(self) -> None:
        bus = HookBus()
        sub(bus, "tool.pre", lambda p: 1 / 0, priority=10, on_failure="deny")
        sub(bus, "tool.pre", lambda p: Ask("check"), priority=60)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertIsInstance(run(bus.gate("tool.pre", {})), Deny)

    def test_a_timeout_fails_toward_ask(self) -> None:
        bus = HookBus()

        async def hang(p: Any) -> None:
            await asyncio.sleep(5)

        sub(bus, "tool.pre", hang, timeout_ms=30)
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertIsInstance(run(bus.gate("tool.pre", {})), Ask)

    def test_on_failure_deny_is_honoured(self) -> None:
        bus = HookBus()
        sub(bus, "tool.pre", lambda p: 1 / 0, on_failure="deny")
        with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
            self.assertIsInstance(run(bus.gate("tool.pre", {})), Deny)

    def test_a_handler_cannot_allow(self) -> None:
        bus = HookBus()
        for verdict in ("allow", True, {"decision": "allow"}):
            d = sub(bus, "tool.pre", lambda p, v=verdict: v)
            with self.assertLogs("personal_os.extensions.hooks", "WARNING"):
                self.assertIsInstance(run(bus.gate("tool.pre", {})), Ask, verdict)
            d.dispose()


class ScopeAndHostTests(unittest.TestCase):
    def test_extension_hooks_must_be_declared_and_are_disposed_with_the_scope(self) -> None:
        host = ExtensionHost()
        scope = host._add(Manifest.from_dict(manifest(hooks={"chat.created": {"priority": 7, "timeoutMs": 40}})))
        seen: list[Any] = []
        with self.assertRaises(ValueError):
            scope.on("chat.deleted", seen.append)
        scope.on("chat.created", seen.append)
        run(host.bus.emit("chat.created", CHAT))
        self.assertEqual(seen, [CHAT])
        sub_ = host.bus._subs["chat.created"][0]
        self.assertEqual((sub_.priority, sub_.timeout_s, sub_.owner), (7, 0.04, "demo"))
        run(scope.stop())
        run(host.bus.emit("chat.created", CHAT))
        self.assertEqual(seen, [CHAT])
        self.assertNotIn("chat.created", host.bus._subs)

    def test_a_module_runs_as_an_extension(self) -> None:
        class M:
            key, label = "mod", "Mod"

            def enabled(self) -> bool:
                return True

            def __init__(self) -> None:
                self.log: list[str] = []

            async def start(self) -> None:
                self.log.append("start")

            async def stop(self) -> None:
                self.log.append("stop")

        host, m = ExtensionHost(), M()
        host.add_module(m)  # type: ignore[arg-type]
        run(host.start())
        run(host.stop())
        self.assertEqual(m.log, ["start", "stop"])
        self.assertEqual(list(host.extensions), ["mod"])

    def test_modules_start_in_order_and_stop_in_reverse(self) -> None:
        log: list[str] = []

        def mod(key: str) -> Any:
            class M:
                label = key.title()

                def enabled(self) -> bool:
                    return True

                async def start(self) -> None:
                    log.append(f"+{key}")

                async def stop(self) -> None:
                    log.append(f"-{key}")
            M.key = key  # type: ignore[attr-defined]
            return M()

        host = ExtensionHost()
        for k in ("a", "b", "c"):
            host.add_module(mod(k))
        run(host.start())
        run(host.stop())
        self.assertEqual(log, ["+a", "+b", "+c", "-c", "-b", "-a"])

    def test_lifespan_runs_startup_then_shutdown_in_registration_order(self) -> None:
        host, log = ExtensionHost(), []

        @host.on_start
        async def s1() -> None:
            log.append("s1")

        @host.on_start
        async def s2() -> None:
            log.append("s2")

        @host.on_stop
        async def d1() -> None:
            log.append("d1")

        @host.on_stop
        async def d2() -> None:
            log.append("d2")

        async def go() -> None:
            async with host.lifespan(None):  # type: ignore[arg-type]
                log.append("serving")

        run(go())
        self.assertEqual(log, ["s1", "s2", "serving", "d1", "d2"])
        self.assertEqual((s1.__name__, d1.__name__), ("s1", "d1"))  # the decorators hand back the function

    def test_shutdown_handlers_run_even_when_the_app_body_raises(self) -> None:
        host, log = ExtensionHost(), []
        host.on_stop(lambda: _append(log, "stopped"))

        async def go() -> None:
            async with host.lifespan(None):  # type: ignore[arg-type]
                raise RuntimeError("crash")

        with self.assertRaises(RuntimeError):
            run(go())
        self.assertEqual(log, ["stopped"])


    def test_a_failing_stop_does_not_keep_the_others_from_stopping(self) -> None:
        host, log = ExtensionHost(), []

        def mod(key: str, fail: bool) -> Any:
            class M:
                label = key

                def enabled(self) -> bool:
                    return True

                async def start(self) -> None: ...

                async def stop(self) -> None:
                    log.append(key)
                    if fail:
                        raise RuntimeError(key)
            M.key = key  # type: ignore[attr-defined]
            return M()

        for k, fail in (("a", False), ("b", True), ("c", False)):
            host.add_module(mod(k, fail))
        with self.assertLogs("personal_os.extensions.host", "ERROR"):
            run(host.stop())
        self.assertEqual(log, ["c", "b", "a"])

    def test_a_failing_shutdown_handler_does_not_skip_the_later_ones(self) -> None:
        host, log = ExtensionHost(), []

        async def boom() -> None:
            raise RuntimeError("boom")

        host.on_stop(boom)
        host.on_stop(lambda: _append(log, "after"))

        async def go() -> None:
            async with host.lifespan(None):  # type: ignore[arg-type]
                pass

        with self.assertLogs("personal_os.extensions.host", "ERROR"):
            run(go())
        self.assertEqual(log, ["after"])


def builtin_ctx(services: dict[str, Any]) -> ExtensionContext:
    return ExtensionContext(db=Database(tempfile.mkdtemp(prefix="exthealth-")), hidden_views=lambda: (), services=services,
                            settings=lambda: {}, set_settings=lambda _s: None, sid=lambda p: p, wsid=lambda p: p, publish=lambda _n, _d: None)


class BuiltinExtensionTests(unittest.TestCase):
    def scope(self, hidden: list[str] | None = None, **over: Any) -> Any:
        self.published: list[tuple[str, Any]] = []
        ctx = ExtensionContext(db=None, hidden_views=lambda: hidden or (), services={"mcp": lambda: "client"},  # type: ignore[arg-type]
                               settings=lambda: {}, set_settings=lambda _s: None, sid=lambda p: p, wsid=lambda p: p,
                               publish=lambda n, d: self.published.append((n, d)))
        return ExtensionHost()._add(Manifest.from_dict(manifest(**over)), ctx=ctx)

    def test_enabled_follows_the_hidden_views_unless_always(self) -> None:
        hidden = ["demo"]
        self.assertFalse(self.scope(hidden).enabled())
        self.assertTrue(self.scope(["other"]).enabled())
        self.assertTrue(self.scope(hidden, enabled="always").enabled())
        self.assertFalse(ExtensionHost()._add(Manifest.from_dict(manifest(enabled=False))).enabled())  # no settings: the manifest default

    def test_tool_and_router_are_registered_and_disposed_with_the_scope(self) -> None:
        scope = self.scope()
        spec = mock.Mock()
        spec.name = "demo_x"
        router = object()
        scope.tool(spec)
        scope.router(router)
        self.assertEqual((scope.tools, scope.routers), ({"demo_x": spec}, [router]))
        run(scope.stop())
        self.assertEqual((scope.tools, scope.routers), ({}, []))

    def test_a_trigger_kind_is_offered_once_and_taken_back_with_the_scope(self) -> None:
        scope = self.scope()
        adapter = mock.Mock()
        scope.trigger("demo-kind", adapter)
        self.assertEqual((list(scope.triggers), scope.triggers["demo-kind"], "demo-kind" in scope.triggers), (["demo-kind"], adapter, True))
        with self.assertRaises(ValueError):
            scope.trigger("demo-kind", adapter)
        run(scope.stop())
        self.assertEqual(list(scope.triggers), [])

    def test_a_folder_extension_cannot_offer_a_trigger_kind(self) -> None:
        scope = self.scope()
        scope.dev = True
        with self.assertRaises(PermissionError):
            scope.trigger("demo-kind", mock.Mock())
        self.assertEqual(list(scope.triggers), [])

    def test_a_trigger_kind_is_polled_only_while_its_extension_is_enabled(self) -> None:
        scope = self.scope(["demo"])
        scope.trigger("demo-kind", mock.Mock())
        self.assertEqual((scope.triggers.enabled("demo-kind"), scope.triggers.enabled("other")), (False, False))
        scope._ctx.hidden_views = lambda: ()
        self.assertTrue(scope.triggers.enabled("demo-kind"))

    def recap_scope(self, hidden: list[str] | None = None) -> tuple[ExtensionHost, Any]:
        host, scope = ExtensionHost(), self.scope(hidden, contributes={"slots": {"recap.section": [{"id": "demo"}]}})
        host.extensions["demo"] = scope
        return host, scope

    def test_a_recap_section_adds_dashboard_keys_and_facts_and_is_disposed_with_the_scope(self) -> None:
        host, scope = self.recap_scope()
        scope.recap_section("demo", card=lambda: {"demo": 1}, facts=lambda: {"demo_facts": 2})
        self.assertEqual((host.recap_cards(), host.recap_facts()), ({"demo": 1}, {"demo_facts": 2}))
        run(scope.stop())
        self.assertEqual((host.recap_cards(), host.recap_facts()), ({}, {}))

    def test_a_recap_section_without_facts_adds_none(self) -> None:
        host, scope = self.recap_scope()
        scope.recap_section("demo", card=lambda: {"demo": 1})
        self.assertEqual((host.recap_cards(), host.recap_facts()), ({"demo": 1}, {}))

    def test_a_recap_section_must_be_declared_in_the_manifest(self) -> None:
        with self.assertRaises(ValueError):
            self.scope().recap_section("demo", card=dict)

    def test_recap_sections_skip_a_disabled_extension(self) -> None:
        host, scope = self.recap_scope(["demo"])
        scope.recap_section("demo", card=lambda: {"demo": 1}, facts=lambda: {"demo_facts": 2})
        self.assertEqual((host.recap_cards(), host.recap_facts()), ({}, {}))

    def test_emit_ui_publishes_an_event_named_for_the_extension(self) -> None:
        scope = self.scope()
        scope.emit_ui("synced", {"n": 3})
        self.assertEqual(self.published, [("ext:demo:synced", {"n": 3})])

    def test_spawned_tasks_are_cancelled_and_awaited_on_stop(self) -> None:
        async def go() -> None:
            scope, done = self.scope(), asyncio.Event()

            async def forever() -> None:
                try:
                    await asyncio.sleep(60)
                finally:
                    done.set()

            async def quick() -> None:
                return None

            task = scope.spawn(forever())
            await scope.spawn(quick())
            await asyncio.sleep(0)
            self.assertEqual(scope.tasks, {task})  # a finished task leaves the set
            await scope.stop()
            self.assertTrue(done.is_set() and task.cancelled() and not scope.tasks)
        run(go())

    def test_a_provided_service_reaches_the_app_and_requirers_until_the_scope_stops(self) -> None:
        scope = self.scope(provides={"services": ["demo"]})
        impl = object()
        scope.provide("demo", impl)
        self.assertIs(scope.app.services["demo"](), impl)
        run(scope.stop())
        self.assertNotIn("demo", scope.app.services)

    def test_provide_refuses_an_undeclared_or_taken_service(self) -> None:
        scope = self.scope(provides={"services": ["demo"]})
        with self.assertRaises(ValueError):
            scope.provide("other", object())
        scope.provide("demo", object())
        with self.assertRaises(ValueError):
            scope.provide("demo", object())

    def test_services_must_be_declared_with_their_permission_and_are_looked_up_late(self) -> None:
        scope = self.scope(requires={"services": ["mcp"]}, permissions=["service:mcp"])
        self.assertEqual(scope.services.get("mcp"), "client")
        with self.assertRaises(KeyError):
            self.scope().services.get("mcp")
        with self.assertRaises(KeyError):
            self.scope(requires={"services": ["mcp"]}).services.get("mcp")

    def test_a_gating_hook_needs_its_permission_in_the_manifest(self) -> None:
        gated = {"tool.pre": ["hooks:gate"], "memory.before_write": ["memory:gate"], "coding.tool.pre": ["hooks:gate", "coding:read"],
                 "coding.tool.post": ["coding:read"]}
        self.scope(hooks={"chat.created": {}}).on("chat.created", lambda p: None)
        for hook, needed in gated.items():
            with self.assertRaises(ManifestError):
                self.scope(hooks={hook: {}})
            with self.assertRaises(ManifestError):
                self.scope(hooks={hook: {}}, permissions=needed[1:])
            self.scope(hooks={hook: {}}, permissions=needed).on(hook, lambda p: None)

    def test_a_precheck_needs_the_gate_permission_and_sees_copies(self) -> None:
        with self.assertRaises(ValueError):
            self.scope().precheck(lambda n, a, c: None)
        scope = self.scope(permissions=["hooks:gate"])
        args = {"path": "/a"}

        def meddle(name: str, a: dict[str, Any], ctx: dict[str, Any]) -> str | None:
            a["path"] = "/elsewhere"
            ctx["settings"] = {"x": 1}
            return None
        scope.precheck(meddle)
        live = {"run_id": "r", "settings": {}, "secret": "s"}
        self.assertIsNone(scope.prechecks.refusal("t", args, live))
        self.assertEqual((args, live["settings"]), ({"path": "/a"}, {}))

    def test_a_disabled_extension_handlers_have_no_opinion_but_lifecycle_hooks_still_arrive(self) -> None:
        seen: list[str] = []
        hidden: list[str] = []
        hooks = {"chat.created": {}, "model.select": {}, "tool.pre": {}, "ext.changed": {}}
        select = {"model": "core", "effort": "high", "reason": "", "tier": "default"}
        pre = {"name": "t", "args": {}, "call_id": "k", "ctx": {"run_id": None, "conversation_id": None, "project_id": None,
                                                              "agent_id": None, "desk_id": None, "tainted": False}}
        scope = self.scope(hidden, hooks=hooks, permissions=["hooks:gate"])

        async def late(p: Any) -> None:
            seen.append("async")
        scope.on("chat.created", lambda p: seen.append("sync"))
        scope.on("model.select", lambda p: {**select, "model": "mine"})
        scope.on("tool.pre", lambda p: Deny("no"))
        scope.on("ext.changed", lambda p: seen.append("changed"))
        scope.on("chat.created", late)
        hidden.append("demo")

        async def go() -> None:
            await scope.bus.emit("chat.created", CHAT)
            await scope.bus.emit("ext.changed", {"id": "demo", "enabled": False})
            self.assertEqual(await scope.bus.pipe("model.select", select), select)
            self.assertIsNone(await scope.bus.gate("tool.pre", pre))
        run(go())
        self.assertEqual(seen, ["changed"])
        hidden.clear()
        run(scope.bus.emit("chat.created", CHAT))
        self.assertCountEqual(seen, ["changed", "sync", "async"])

    def test_a_disabled_extension_channel_and_routes_are_out_of_reach(self) -> None:
        from fastapi import APIRouter, FastAPI
        from fastapi.testclient import TestClient
        hidden: list[str] = []
        host = ExtensionHost()
        scope = self.scope(hidden)
        host.extensions["demo"] = scope
        adapter = mock.Mock(is_texts_conversation=lambda c: c == "chat")
        adapter.name = "demo-chan"
        scope.channel(adapter)
        router = APIRouter(prefix="/demo")
        router.add_api_route("/ping", lambda: {"ok": True})
        scope.router(router)
        app = FastAPI()
        host.mount(app, scope)
        client = TestClient(app)
        self.assertEqual((scope.channels.owner_of("chat"), client.get("/demo/ping").json()), (adapter, {"ok": True}))
        hidden.append("demo")
        self.assertEqual((list(scope.channels), scope.channels.owner_of("chat"), client.get("/demo/ping").status_code), ([], None, 404))
        self.assertEqual(scope.channels.registered(), [adapter])

    def test_load_builtin_activates_health_and_starts_and_stops_its_loop(self) -> None:
        host = ExtensionHost()
        ctx = builtin_ctx({"mcp": lambda: None})
        scope = host.load_builtin("health", ctx)
        self.assertEqual(list(scope.tools), ["health_summary", "health_log", "health_delete_entry"])
        self.assertEqual([r.prefix for r in scope.routers], ["/health"])
        self.assertEqual(list(host.bus._subs), ["app.start"])

        async def go() -> None:
            await host.start()
            self.assertEqual(len(scope.tasks), 1)
            await host.stop()
            self.assertEqual(scope.tasks, set())
        run(go())

    def test_load_builtin_activates_todos_provides_its_store_and_runs_the_sync_loop(self) -> None:
        host = ExtensionHost()
        ctx = builtin_ctx({"google": lambda: None})
        scope = host.load_builtin("todos", ctx)
        self.assertEqual(list(scope.tools), ["todo_list", "todo_add", "todo_update", "todo_delete"])
        self.assertTrue({"todos", "tasks_sync"} <= set(ctx.services))
        self.assertIs(ctx.services["tasks_sync"]().todos, ctx.services["todos"]())
        self.assertEqual(list(host.bus._subs), ["app.start"])

        async def go() -> None:
            await host.start()
            self.assertEqual(len(scope.tasks), 1)
            await host.stop()
            self.assertEqual(scope.tasks, set())
        run(go())
        self.assertFalse({"todos", "tasks_sync"} & set(ctx.services))

    def test_todos_services_stay_provided_when_the_user_hides_lists(self) -> None:
        ctx = builtin_ctx({"google": lambda: None})
        ctx.hidden_views = lambda: ("todos",)
        scope = ExtensionHost().load_builtin("todos", ctx)
        self.assertFalse(scope.enabled())
        self.assertTrue({"todos", "tasks_sync"} <= set(ctx.services))

    def test_load_builtin_spaces_provides_the_store_and_its_tools_and_stays_on_when_hidden(self) -> None:
        ctx = builtin_ctx({})
        ctx.hidden_views = lambda: ("spaces",)
        scope = ExtensionHost().load_builtin("spaces", ctx)
        self.assertEqual(list(scope.tools), ["space_list", "space_add_widget", "space_arrange"])
        self.assertTrue(scope.enabled())
        self.assertIs(ctx.services["canvases"]().db, ctx.db)
        paths = {route.path for r in scope.routers for route in r.routes}  # type: ignore[attr-defined]
        self.assertTrue({"/canvases", "/canvases/{id}/windows", "/windows/{wid}"} <= paths)

    def test_widget_kinds_are_the_declared_ones_enabled_or_not(self) -> None:
        host = ExtensionHost()
        host._add(Manifest.from_dict(manifest(enabled=False, contributes={"slots": {"canvas.widget": [{"id": "ext:demo:clock"}]}})))
        self.assertEqual(host.widget_kinds(), ("ext:demo:clock",))

    def test_spaces_accepts_a_declared_widget_kind_and_drops_windows_of_a_removed_one_on_start(self) -> None:
        host = ExtensionHost()
        ctx = builtin_ctx({})
        ctx.widget_kinds = host.widget_kinds
        host._add(Manifest.from_dict(manifest(contributes={"slots": {"canvas.widget": [{"id": "ext:demo:clock"}]}})))
        host.load_builtin("spaces", ctx)
        canvases = ctx.services["canvases"]()
        cid = canvases.list()[0]["id"]
        for kind in ("ext:demo:clock", "ext:gone:old", "todos"):
            canvases.add_window(cid, kind)
        run(host.start())
        self.assertEqual([w["kind"] for w in canvases.get(cid)["windows"]], ["ext:demo:clock", "todos"])

    def test_spaces_routes_and_tool_schema_follow_the_kinds_declared_by_extensions_loaded_after_it(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        host = ExtensionHost()
        ctx = builtin_ctx({})
        ctx.widget_kinds = host.widget_kinds
        scope = host.load_builtin("spaces", ctx)
        host._add(Manifest.from_dict(manifest(contributes={"slots": {"canvas.widget": [{"id": "ext:demo:clock"}]}})))
        cid = ctx.services["canvases"]().list()[0]["id"]
        app = FastAPI()
        host.mount(app, scope)
        client = TestClient(app)
        self.assertEqual(client.post(f"/canvases/{cid}/windows", json={"kind": "ext:demo:clock"}).status_code, 200)
        self.assertEqual(client.post(f"/canvases/{cid}/windows", json={"kind": "ext:demo:nope"}).status_code, 400)
        enum = scope.tools["space_add_widget"].parameters["properties"]["kind"]["enum"]
        self.assertNotIn("ext:demo:clock", enum)
        run(host.start())
        self.assertIn("ext:demo:clock", enum)

    def test_load_builtin_google_provides_the_client_to_requirers_only(self) -> None:
        host = ExtensionHost()
        ctx = builtin_ctx({"mcp": lambda: None})
        google = host.load_builtin("google", ctx)
        self.assertEqual(google.manifest.enabled, "always")
        self.assertEqual(google.manifest.provides_services, ("google",))
        self.assertEqual(google.manifest.contributes.get("tools"), None)
        todos = host.load_builtin("todos", ctx)
        health = host.load_builtin("health", ctx)
        client = ctx.services["google"]()
        self.assertIs(todos.services.get("google"), client)
        self.assertEqual(client._reads.path, Path(ctx.db.data_dir) / "google-reads.json")
        with self.assertRaises(KeyError):
            health.services.get("google")
        run(google.stop())
        self.assertNotIn("google", ctx.services)

    def test_google_stays_on_when_the_user_hides_every_view(self) -> None:
        ctx = builtin_ctx({})
        ctx.hidden_views = lambda: ("google",)
        self.assertTrue(ExtensionHost().load_builtin("google", ctx).enabled())

    def test_load_builtin_refuses_tools_the_manifest_does_not_declare(self) -> None:
        real = load_manifest(Path(__file__).resolve().parents[1] / "personal_os" / "extensions" / "health" / "extension.json")
        short = Manifest.from_dict({"id": "health", "name": "Health", "version": "1.0.0", "apiVersion": 1,
                                    "hooks": dict(real.hooks), "contributes": {"tools": [{"name": "health_log", "danger": "writes", "group": "health"}]}})
        ctx = builtin_ctx({})
        with mock.patch("personal_os.extensions.host.load_manifest", return_value=short), self.assertRaises(ValueError):
            ExtensionHost().load_builtin("health", ctx)

    def test_load_builtin_refuses_a_danger_the_code_does_not_register(self) -> None:
        real = load_manifest(Path(__file__).resolve().parents[1] / "personal_os" / "extensions" / "health" / "extension.json")
        tools = [{**t, "danger": "safe"} if t["name"] == "health_log" else t for t in real.contributes["tools"]]
        drifted = Manifest.from_dict({"id": "health", "name": "Health", "version": "1.0.0", "apiVersion": 1,
                                      "hooks": dict(real.hooks), "contributes": {"tools": tools}})
        ctx = builtin_ctx({})
        with mock.patch("personal_os.extensions.host.load_manifest", return_value=drifted), self.assertRaises(ValueError):
            ExtensionHost().load_builtin("health", ctx)


async def _append(log: list[str], v: str) -> None:
    log.append(v)


if __name__ == "__main__":
    unittest.main()
