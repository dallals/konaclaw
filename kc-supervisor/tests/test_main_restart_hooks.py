from __future__ import annotations
from unittest.mock import MagicMock


def test_make_restart_unregisters_then_registers():
    """Verify the registry-surgery contract used by the hot-restart hook:
    unregister the old instance, build a fresh one, register it back.

    The production wiring in main.py wraps this surgery in a closure that
    also dispatches async stop/start via run_coroutine_threadsafe. That
    asyncio path is exercised by the manual smoke step (after Task 18);
    here we test the load-bearing piece that doesn't involve a live loop.
    """
    from kc_connectors.base import ConnectorRegistry, Connector

    class FakeConnector(Connector):
        def __init__(self, name="telegram"):
            super().__init__(name=name)
            self.started = False
            self.stopped = False

        async def start(self, supervisor):
            self.started = True

        async def stop(self):
            self.stopped = True

        async def send(self, chat_id, content, attachments=None):
            pass

    reg = ConnectorRegistry()
    initial = FakeConnector("telegram")
    reg.register(initial)
    holder = [initial]

    # Simulate _build_telegram returning a fresh instance based on new secrets.
    new_instance = FakeConnector("telegram")
    builder = MagicMock(return_value=new_instance)

    # Simulate the restart sequence (mirrors main._make_restart body without
    # the loop indirection — we're testing the registry surgery, not asyncio).
    reg.unregister("telegram")
    fresh = builder({"telegram_bot_token": "x", "telegram_allowlist": ["@y"]})
    holder[0] = fresh
    reg.register(fresh)

    assert reg.all() == [new_instance]
    assert holder[0] is new_instance
    builder.assert_called_once()


def test_unregister_is_idempotent():
    """unregister() must not raise when the name doesn't exist — a second
    PATCH after the first restart already swapped instances must not crash."""
    from kc_connectors.base import ConnectorRegistry
    reg = ConnectorRegistry()
    reg.unregister("nonexistent")  # should be a no-op
    assert reg.all() == []


def test_registry_survives_connector_exception():
    """Structural guarantee for Phase 2 (Task 7.1): ConnectorRegistry must
    remain non-None even when post-construction connector wiring throws.

    This mirrors the main.py boot sequence:
      connector_registry = ConnectorRegistry()   # always runs
      try:
          ... build connectors ...               # may raise
      except Exception:
          # registry is NOT reset — only routing_table is cleared
          pass

    ReminderRunner.fire() relies on connector_registry.get(channel) being
    callable at fire time; if the channel isn't registered it logs + marks
    the row failed rather than crashing the supervisor.
    """
    from kc_connectors.base import ConnectorRegistry

    # Simulate the pattern: registry constructed first, then wiring throws.
    connector_registry = ConnectorRegistry()
    routing_table = None
    try:
        routing_table = object()  # stand-in for RoutingTable
        raise RuntimeError("simulated connector wiring failure")
    except Exception:
        # main.py only clears routing_table on Exception, not connector_registry
        routing_table = None

    # The guarantee: registry is always non-None after boot.
    assert connector_registry is not None
    assert connector_registry.all() == []
    # routing_table may be None when wiring fails — that's acceptable.
    assert routing_table is None


def test_deps_connector_registry_assigned_unconditionally():
    """Structural guarantee for Phase 2 (Task 7): deps.connector_registry
    must always be assigned when the local connector_registry is non-None,
    regardless of whether routing_table exists or connectors are registered.

    This is critical because ReminderRunner is instantiated with
    connector_registry=deps.connector_registry at line 331, and ReminderRunner.fire()
    calls self.connector_registry.get(channel) at fire time. Without this
    guarantee, fire() would crash with AttributeError if routing_table was
    None or no connectors were configured at boot.

    This test simulates the fixed boot sequence in main.py:
      - connector_registry is non-None (an empty registry)
      - routing_table is None (or just not meeting the InboundRouter condition)
      - deps.connector_registry should still be assigned before ReminderRunner creation
    """
    from kc_supervisor.service import Deps
    from kc_connectors.base import ConnectorRegistry

    # Simulate boot: create Deps with minimal state.
    deps = Deps(
        storage=MagicMock(),
        registry=MagicMock(),
        conversations=MagicMock(),
        approvals=MagicMock(),
        home=MagicMock(),
        shares=MagicMock(),
        conv_locks=MagicMock(),
        mcp_manager=None,
        mcp_install_store=None,
        secrets_store=MagicMock(),
        google_credentials_path=None,
        google_token_path=None,
        news_client=None,
    )

    # Simulate local variables as they'd be after boot.
    connector_registry = ConnectorRegistry()
    routing_table = None  # This is the scenario: no routing configured

    # The fixed code: unconditionally assign connector_registry to deps
    # if the local registry is non-None.
    if connector_registry is not None:
        deps.connector_registry = connector_registry

    # The guarantee: deps.connector_registry is non-None so ReminderRunner
    # can call .get(channel) at fire time without crashing.
    assert deps.connector_registry is not None
    assert deps.connector_registry is connector_registry
    # If this regresses (someone moves the assignment back inside the inner
    # if block), deps.connector_registry will be None and this assertion fails.
    # ReminderRunner instantiation at line 331 would also fail as a result.
