"""Tests for BlackRoad Event Sourcing"""
import pytest
from event_sourcing import (
    Event, Aggregate, Projection, Snapshot, Command,
    EventStore, CommandBus, ProjectionManager, EventSourcingSystem,
)


@pytest.fixture
def store():
    return EventStore(":memory:")


@pytest.fixture
def system():
    return EventSourcingSystem(":memory:")


def make_event(aggregate_id="agg-1", event_type="Created", version=1, payload=None):
    return Event.create(
        aggregate_id=aggregate_id,
        aggregate_type="Order",
        event_type=event_type,
        payload=payload or {"status": "created"},
        version=version,
    )


class TestEvent:
    def test_create_event(self):
        evt = make_event()
        assert evt.aggregate_id == "agg-1"
        assert evt.event_type == "Created"
        assert evt.id is not None
        assert evt.timestamp is not None

    def test_event_to_dict(self):
        evt = make_event()
        d = evt.to_dict()
        assert d["aggregate_id"] == "agg-1"
        assert "payload" in d

    def test_event_caused_by(self):
        evt = Event.create("a", "Order", "Shipped", {}, 2, caused_by="cmd-123")
        assert evt.caused_by == "cmd-123"


class TestAggregate:
    def test_aggregate_apply(self):
        agg = Aggregate(id="a1", type="Order")
        evt = make_event("a1", "Created", 1, {"status": "new"})
        agg.apply(evt)
        assert agg.version == 1
        assert agg.state["status"] == "new"

    def test_aggregate_raise_event(self):
        agg = Aggregate(id="a1", type="Order")
        evt = agg.raise_event("Created", {"status": "pending"})
        assert evt.version == 1
        assert agg.version == 1


class TestEventStore:
    def test_append_and_load(self, store):
        evt = make_event("agg-1", version=1)
        store.append("agg-1", [evt])
        events = store.load("agg-1")
        assert len(events) == 1
        assert events[0].event_type == "Created"

    def test_load_from_version(self, store):
        for i in range(1, 4):
            store.append("agg-1", [make_event("agg-1", version=i)])
        events = store.load("agg-1", from_version=1)
        assert len(events) == 2
        assert events[0].version == 2

    def test_load_all_by_type(self, store):
        store.append("a1", [Event.create("a1", "Order", "Created", {}, 1)])
        store.append("b1", [Event.create("b1", "Order", "Created", {}, 1)])
        store.append("c1", [Event.create("c1", "User", "Registered", {}, 1)])
        events = store.load_all("Order")
        assert len(events) == 2

    def test_snapshot_creation(self, store):
        store.append("agg-1", [
            Event.create("agg-1", "Order", "Created", {"status": "new"}, 1),
            Event.create("agg-1", "Order", "Updated", {"status": "paid"}, 2),
        ])
        snap = store.create_snapshot("agg-1")
        assert snap is not None
        assert snap.version == 2
        assert snap.state["status"] == "paid"

    def test_load_snapshot(self, store):
        store.append("agg-1", [make_event("agg-1", version=1)])
        store.create_snapshot("agg-1")
        snap = store.load_snapshot("agg-1")
        assert snap is not None
        assert snap.aggregate_id == "agg-1"

    def test_no_snapshot_returns_none(self, store):
        snap = store.load_snapshot("nonexistent")
        assert snap is None

    def test_reconstruct_aggregate(self, store):
        store.append("agg-1", [
            Event.create("agg-1", "Order", "Created", {"status": "new", "total": 0}, 1),
            Event.create("agg-1", "Order", "Updated", {"total": 100}, 2),
        ])
        agg = store.reconstruct("agg-1", "Order")
        assert agg.version == 2
        assert agg.state["total"] == 100

    def test_reconstruct_with_snapshot(self, store):
        store.append("agg-1", [
            Event.create("agg-1", "Order", "Created", {"status": "new"}, 1),
        ])
        store.create_snapshot("agg-1")
        store.append("agg-1", [
            Event.create("agg-1", "Order", "Shipped", {"status": "shipped"}, 2),
        ])
        agg = store.reconstruct("agg-1", "Order")
        assert agg.state["status"] == "shipped"

    def test_get_position(self, store):
        assert store.get_position() == 0
        store.append("agg-1", [make_event("agg-1", version=1)])
        assert store.get_position() == 1


class TestCommandBus:
    def test_dispatch_with_handler(self, store):
        bus = CommandBus(store)
        def handler(cmd, es):
            es.append(cmd.payload["aggregate_id"], [
                Event.create(cmd.payload["aggregate_id"], "Order", "Created", cmd.payload, 1)
            ])
            return {"id": cmd.payload["aggregate_id"]}
        bus.register("CreateOrder", handler)
        result = bus.dispatch("CreateOrder", {"aggregate_id": "o1", "total": 50})
        assert result["status"] == "ok"

    def test_dispatch_no_handler(self, store):
        bus = CommandBus(store)
        result = bus.dispatch("UnknownCommand", {})
        assert result["status"] == "error"


class TestProjection:
    def test_projection_handles_event(self, store):
        proj = Projection("orders")
        counts = {"count": 0}
        proj.state = counts
        def on_created(state, event):
            state["count"] = state.get("count", 0) + 1
        proj.handlers["Created"] = on_created

        evt = make_event("a1", "Created", 1)
        proj.handle(evt)
        assert counts["count"] == 1

    def test_rebuild_projection(self, system):
        orders_state = {}
        proj = Projection("order_summary", state=orders_state)
        proj.handlers["OrderCreated"] = lambda state, evt: state.update({evt.aggregate_id: evt.payload})
        system.projections.register(proj)

        system.store.append("o1", [Event.create("o1", "Order", "OrderCreated", {"total": 100}, 1)])
        system.store.append("o2", [Event.create("o2", "Order", "OrderCreated", {"total": 200}, 1)])

        count = system.rebuild_projection("order_summary")
        assert count == 2
        assert "o1" in system.query_projection("order_summary")

    def test_query_projection_key(self, system):
        proj = Projection("kv")
        proj.handlers["Set"] = lambda state, evt: state.update({evt.payload["k"]: evt.payload["v"]})
        system.projections.register(proj)
        system.store.append("x", [Event.create("x", "T", "Set", {"k": "color", "v": "blue"}, 1)])
        system.rebuild_projection("kv")
        assert system.query_projection("kv", "color") == "blue"


class TestSystem:
    def test_statistics(self, system):
        system.store.append("a1", [
            Event.create("a1", "Order", "Created", {}, 1),
            Event.create("a1", "Order", "Updated", {}, 2),
        ])
        stats = system.statistics()
        assert stats["total_events"] == 2
        assert "Created" in stats["by_type"]

    def test_aggregate_history(self, system):
        system.store.append("a1", [
            Event.create("a1", "Order", "Created", {"status": "new"}, 1),
        ])
        history = system.get_aggregate_history("a1")
        assert len(history) == 1
        assert history[0]["event_type"] == "Created"
