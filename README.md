# blackroad-event-sourcing

> Event sourcing and CQRS framework with projections and snapshots — part of the BlackRoad OS developer platform.

## Features

- 📝 **Event Store** — Append-only SQLite event log with position tracking
- 🔄 **CQRS** — Command/Query responsibility separation with command bus
- 📊 **Projections** — Rebuild read-models from event streams
- 📸 **Snapshots** — Aggregate state snapshots for fast reconstruction
- 🏗️ **Aggregate Reconstruction** — Replay events from snapshot + delta
- 🔍 **Event Queries** — Load by aggregate, type, or position

## Quick Start

```python
from event_sourcing import EventSourcingSystem, Event, Projection

system = EventSourcingSystem()

# Append events
evt = Event.create("order-1", "Order", "OrderCreated", {"total": 99.99}, version=1)
system.store.append("order-1", [evt])

# Register projection
proj = Projection("order_totals")
proj.handlers["OrderCreated"] = lambda state, evt: state.update({evt.aggregate_id: evt.payload["total"]})
system.projections.register(proj)

# Rebuild and query
system.rebuild_projection("order_totals")
total = system.query_projection("order_totals", "order-1")  # 99.99

# Register command handler
def handle_create_order(cmd, store):
    evt = Event.create(cmd.payload["id"], "Order", "OrderCreated", cmd.payload, 1)
    store.append(cmd.payload["id"], [evt])
    return {"id": cmd.payload["id"]}

system.command_bus.register("CreateOrder", handle_create_order)
result = system.dispatch_command("CreateOrder", {"id": "order-2", "total": 50.0})
```

## Running Tests

```bash
pip install pytest pytest-cov
pytest tests/ -v --cov=event_sourcing
```

## License

Proprietary — © BlackRoad OS, Inc.
