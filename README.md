# blackroad-event-sourcing

[![PyPI version](https://img.shields.io/pypi/v/blackroad-event-sourcing.svg)](https://pypi.org/project/blackroad-event-sourcing/)
[![Python](https://img.shields.io/pypi/pyversions/blackroad-event-sourcing.svg)](https://pypi.org/project/blackroad-event-sourcing/)
[![License](https://img.shields.io/badge/license-Proprietary-red.svg)](LICENSE)
[![Tests](https://github.com/BlackRoad-OS/blackroad-event-sourcing/actions/workflows/ci.yml/badge.svg)](https://github.com/BlackRoad-OS/blackroad-event-sourcing/actions)

> **Production-grade Event Sourcing & CQRS framework** with projections, snapshots, and first-class Stripe webhook support — part of the [BlackRoad OS](https://blackroad.dev) developer platform.

---

## Index

1. [Overview](#overview)
2. [Features](#features)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Core Concepts](#core-concepts)
   - [Events](#events)
   - [Aggregates](#aggregates)
   - [Commands & Command Bus](#commands--command-bus)
   - [Projections](#projections)
   - [Snapshots](#snapshots)
6. [Stripe Integration](#stripe-integration)
7. [API Reference](#api-reference)
   - [Event](#event)
   - [Aggregate](#aggregate)
   - [EventStore](#eventstore)
   - [CommandBus](#commandbus)
   - [ProjectionManager](#projectionmanager)
   - [EventSourcingSystem](#eventsourcingsystem)
8. [Testing](#testing)
9. [License](#license)

---

## Overview

`blackroad-event-sourcing` provides a lightweight, **append-only event store** built on SQLite that implements the full Event Sourcing and CQRS (Command Query Responsibility Segregation) pattern. Every state change is recorded as an immutable event, giving you a complete audit trail, reliable projections, and the ability to reconstruct any aggregate at any point in time.

Designed for production workloads — including payment processing with Stripe — with zero external dependencies beyond the Python standard library.

---

## Features

| Feature | Description |
|---|---|
| 📝 **Event Store** | Append-only SQLite log with automatic position tracking and indexed queries |
| 🔄 **CQRS** | Command/Query separation via a typed `CommandBus` with full command logging |
| 📊 **Projections** | Incremental or full-rebuild read-models from any event stream |
| 📸 **Snapshots** | Point-in-time aggregate snapshots for fast reconstruction at scale |
| 🏗️ **Aggregate Reconstruction** | Replay events from snapshot + delta — efficient at any version |
| 💳 **Stripe Ready** | Built-in patterns for ingesting Stripe webhook events as first-class domain events |
| 🔍 **Event Queries** | Load by aggregate ID, aggregate type, global position, or custom filters |
| 📈 **Statistics** | Real-time event counts and type breakdowns via `system.statistics()` |

---

## Installation

```bash
pip install blackroad-event-sourcing
```

**Requirements:** Python 3.8+, no external runtime dependencies.

To install development dependencies (tests):

```bash
pip install blackroad-event-sourcing[dev]
# or directly:
pip install pytest pytest-cov
```

---

## Quick Start

```python
from event_sourcing import EventSourcingSystem, Event, Projection

# In-memory store (use a file path for persistence: db_path="events.db")
system = EventSourcingSystem()

# 1. Append events directly
evt = Event.create("order-1", "Order", "OrderCreated", {"total": 99.99}, version=1)
system.store.append("order-1", [evt])

# 2. Register a projection
proj = Projection("order_totals")
proj.handlers["OrderCreated"] = lambda state, evt: state.update(
    {evt.aggregate_id: evt.payload["total"]}
)
system.projections.register(proj)

# 3. Rebuild and query the read-model
system.rebuild_projection("order_totals")
total = system.query_projection("order_totals", "order-1")  # → 99.99

# 4. Dispatch commands via the command bus
def handle_create_order(cmd, store):
    evt = Event.create(cmd.payload["id"], "Order", "OrderCreated", cmd.payload, version=1)
    store.append(cmd.payload["id"], [evt])
    return {"id": cmd.payload["id"]}

system.command_bus.register("CreateOrder", handle_create_order)
result = system.dispatch_command("CreateOrder", {"id": "order-2", "total": 50.0})
# → {"status": "ok", "result": {"id": "order-2"}}
```

---

## Core Concepts

### Events

An `Event` is an **immutable record** of something that happened in your domain. Events are the single source of truth.

```python
from event_sourcing import Event

evt = Event.create(
    aggregate_id="order-42",
    aggregate_type="Order",
    event_type="OrderShipped",
    payload={"carrier": "FedEx", "tracking": "123ABC"},
    version=3,
    caused_by="cmd-uuid-here",   # optional: links to the command that caused it
)
print(evt.to_dict())
```

### Aggregates

An `Aggregate` encapsulates domain state. Override `apply()` to define how each event mutates state.

```python
from event_sourcing import Aggregate, Event

class OrderAggregate(Aggregate):
    def apply(self, event: Event):
        self.version = event.version
        if event.event_type == "OrderCreated":
            self.state["status"] = "created"
            self.state["total"] = event.payload["total"]
        elif event.event_type == "OrderShipped":
            self.state["status"] = "shipped"

order = OrderAggregate(id="order-42", type="Order")
evt = order.raise_event("OrderCreated", {"total": 149.00})
# order.state → {"total": 149.00}
```

### Commands & Command Bus

Commands are **intentions** to change state. The `CommandBus` routes them to handlers, logs every dispatch, and returns a structured result.

```python
from event_sourcing import EventSourcingSystem, Event

system = EventSourcingSystem(db_path="production.db")

def handle_place_order(cmd, store):
    evt = Event.create(
        cmd.payload["order_id"], "Order", "OrderPlaced", cmd.payload, version=1
    )
    store.append(cmd.payload["order_id"], [evt])
    return {"order_id": cmd.payload["order_id"]}

system.command_bus.register("PlaceOrder", handle_place_order)
result = system.dispatch_command(
    "PlaceOrder",
    {"order_id": "o-99", "total": 250.00},
    issued_by="user-123",
)
```

### Projections

A `Projection` builds a **read-model** from the event stream. Register event-type handlers, then rebuild or advance incrementally.

```python
from event_sourcing import Projection

revenue = Projection("revenue_by_day")

def on_order_placed(state, evt):
    day = evt.timestamp[:10]  # YYYY-MM-DD
    state[day] = state.get(day, 0) + evt.payload.get("total", 0)

revenue.handlers["OrderPlaced"] = on_order_placed
system.projections.register(revenue)
system.rebuild_projection("revenue_by_day")
today = system.query_projection("revenue_by_day", "2025-01-15")
```

### Snapshots

Snapshots avoid replaying the full event history on every load. Create them periodically; reconstruction automatically starts from the latest snapshot.

```python
# Create a snapshot at the current aggregate version
snap = system.store.create_snapshot("order-42")
# snap.version, snap.state, snap.created_at

# Reconstruct — transparently uses snapshot + subsequent events
agg = system.store.reconstruct("order-42", "Order")
```

---

## Stripe Integration

`blackroad-event-sourcing` is purpose-built for ingesting **Stripe webhook events** as immutable domain events, giving you a complete payment audit trail and real-time read-models.

### Ingesting Stripe Webhooks

```python
import stripe
from event_sourcing import EventSourcingSystem, Event, Projection

system = EventSourcingSystem(db_path="payments.db")

# --- Projection: live payment status per charge ---
payment_status = Projection("payment_status")
payment_status.handlers["payment_intent.succeeded"] = lambda state, evt: state.update(
    {evt.aggregate_id: {"status": "succeeded", "amount": evt.payload.get("amount")}}
)
payment_status.handlers["payment_intent.payment_failed"] = lambda state, evt: state.update(
    {evt.aggregate_id: {"status": "failed", "error": evt.payload.get("last_payment_error")}}
)
payment_status.handlers["charge.refunded"] = lambda state, evt: state.update(
    {evt.aggregate_id: {"status": "refunded", "amount_refunded": evt.payload.get("amount_refunded")}}
)
system.projections.register(payment_status)


def ingest_stripe_webhook(stripe_event: dict):
    """
    Convert a Stripe webhook event dict (from stripe.Webhook.construct_event)
    into a domain Event and append it to the store.
    """
    stripe_type = stripe_event["type"]          # e.g. "payment_intent.succeeded"
    obj = stripe_event["data"]["object"]
    aggregate_id = obj.get("id")                # e.g. "pi_3P..." or "ch_3P..."
    aggregate_type = obj.get("object", "stripe_object").replace(".", "_")

    evt = Event.create(
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        event_type=stripe_type,
        payload=obj,
        version=1,
        caused_by=stripe_event.get("id"),       # Stripe event ID as causation
    )
    system.store.append(aggregate_id, [evt])
    system.projections.advance_all()            # keep read-models current
    return evt


# Example: Flask/FastAPI webhook endpoint
# @app.post("/webhooks/stripe")
# def stripe_webhook(request):
#     payload = request.body
#     sig = request.headers["Stripe-Signature"]
#     event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
#     ingest_stripe_webhook(event)
#     return {"status": "ok"}


# Query the live read-model
status = system.query_projection("payment_status", "pi_3P_example")
# → {"status": "succeeded", "amount": 9999}
```

### Replaying Payment History

```python
# Full history for a single payment intent
history = system.get_aggregate_history("pi_3P_example")
for record in history:
    print(record["event_type"], record["timestamp"], record["payload"]["amount"])

# System-wide payment statistics
stats = system.statistics()
print(stats["total_events"])           # total events across all aggregates
print(stats["by_type"])                # counts per Stripe event type
```

---

## API Reference

### `Event`

| Member | Type | Description |
|---|---|---|
| `id` | `str` | UUID auto-generated on creation |
| `aggregate_id` | `str` | ID of the owning aggregate |
| `aggregate_type` | `str` | Type name of the aggregate |
| `event_type` | `str` | Domain event name (e.g. `"OrderCreated"`) |
| `payload` | `dict` | Arbitrary event data |
| `version` | `int` | Aggregate version after this event |
| `timestamp` | `str` | UTC ISO-8601 timestamp |
| `caused_by` | `str \| None` | ID of the command or external event that triggered this |
| `metadata` | `dict` | Optional extra key/value pairs |

**`Event.create(aggregate_id, aggregate_type, event_type, payload, version, caused_by=None, **metadata)`**
Factory method — preferred way to create events.

**`event.to_dict()`** → `dict`
Serialize the event to a plain dictionary.

---

### `Aggregate`

| Member | Type | Description |
|---|---|---|
| `id` | `str` | Aggregate identifier |
| `type` | `str` | Aggregate type name |
| `version` | `int` | Current version (incremented on each event) |
| `state` | `dict` | Current state dictionary |

**`aggregate.apply(event)`**
Apply an event to state. Override in subclasses.

**`aggregate.raise_event(event_type, payload, caused_by=None)`** → `Event`
Increment version, create event, apply it, and return it (does **not** persist — call `store.append()`).

---

### `EventStore`

**`EventStore(db_path=":memory:")`**
Create a store backed by SQLite. Use `":memory:"` for tests or a file path for persistence.

| Method | Returns | Description |
|---|---|---|
| `append(aggregate_id, events)` | `List[int]` | Append events and return their global positions |
| `load(aggregate_id, from_version=0)` | `List[Event]` | Load events for an aggregate from a given version |
| `load_all(aggregate_type, after_position=0)` | `List[Event]` | Load all events of a given aggregate type |
| `load_all_events(after_position=0)` | `List[Event]` | Load all events in the store |
| `get_position()` | `int` | Latest global event position |
| `create_snapshot(aggregate_id)` | `Snapshot \| None` | Snapshot current state for an aggregate |
| `load_snapshot(aggregate_id)` | `Snapshot \| None` | Load the latest snapshot |
| `reconstruct(aggregate_id, aggregate_type)` | `Aggregate` | Reconstruct aggregate from snapshot + events |

---

### `CommandBus`

**`CommandBus(event_store)`**

| Method | Returns | Description |
|---|---|---|
| `register(cmd_type, handler)` | `None` | Register a handler `(cmd, store) → dict` for a command type |
| `dispatch(cmd_type, payload, issued_by=None)` | `dict` | Dispatch a command; returns `{"status": "ok", "result": ...}` or `{"status": "error", "message": ...}` |

---

### `ProjectionManager`

**`ProjectionManager(event_store)`**

| Method | Returns | Description |
|---|---|---|
| `register(projection)` | `None` | Register a projection (persists/restores position & state) |
| `rebuild_projection(name)` | `int` | Full rebuild from position 0; returns events processed |
| `advance(name)` | `int` | Process only new events since last position |
| `advance_all()` | `Dict[str, int]` | Advance all registered projections |
| `query_projection(name, key=None)` | `Any` | Return full state dict or a single key |

---

### `EventSourcingSystem`

High-level façade that wires together `EventStore`, `CommandBus`, and `ProjectionManager`.

**`EventSourcingSystem(db_path=":memory:")`**

| Member | Type | Description |
|---|---|---|
| `store` | `EventStore` | Direct access to the event store |
| `command_bus` | `CommandBus` | Direct access to the command bus |
| `projections` | `ProjectionManager` | Direct access to projection management |

| Method | Returns | Description |
|---|---|---|
| `dispatch_command(cmd_type, payload, issued_by=None)` | `dict` | Shortcut for `command_bus.dispatch()` |
| `rebuild_projection(name)` | `int` | Shortcut for `projections.rebuild_projection()` |
| `query_projection(name, key=None)` | `Any` | Shortcut for `projections.query_projection()` |
| `get_aggregate_history(aggregate_id)` | `List[dict]` | All events for an aggregate as plain dicts |
| `statistics()` | `dict` | `{"total_events", "by_type", "latest_position"}` |

---

## Testing

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run the full test suite with coverage
pytest tests/ -v --cov=event_sourcing
```

The test suite covers:
- Event creation and serialization
- Aggregate state application and event raising
- Event store append / load / version filtering
- Snapshot creation, loading, and aggregate reconstruction
- Command bus dispatch with and without handlers
- Projection registration, rebuild, and incremental advance
- System-level statistics and aggregate history

---

## License

Proprietary — © BlackRoad OS, Inc. All rights reserved.

For licensing inquiries, contact [hello@blackroad.dev](mailto:hello@blackroad.dev).
