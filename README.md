# blackroad-event-sourcing

[![CI](https://github.com/BlackRoad-OS/blackroad-event-sourcing/actions/workflows/ci.yml/badge.svg)](https://github.com/BlackRoad-OS/blackroad-event-sourcing/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20|%203.11%20|%203.12-blue.svg)](https://www.python.org/)
[![License: Proprietary](https://img.shields.io/badge/license-Proprietary-red.svg)](./LICENSE)
[![BlackRoad OS](https://img.shields.io/badge/BlackRoad-OS-black.svg)](https://blackroad.dev)

> **Production-grade** Event Sourcing and CQRS framework with projections, snapshots, and a built-in command bus — part of the [BlackRoad OS](https://blackroad.dev) developer platform.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Core Concepts](#core-concepts)
   - [Event](#event)
   - [Aggregate](#aggregate)
   - [Event Store](#event-store)
   - [Command Bus (CQRS)](#command-bus-cqrs)
   - [Projections](#projections)
   - [Snapshots](#snapshots)
6. [API Reference](#api-reference)
   - [Event](#event-api)
   - [Aggregate](#aggregate-api)
   - [EventStore](#eventstore-api)
   - [CommandBus](#commandbus-api)
   - [ProjectionManager](#projectionmanager-api)
   - [EventSourcingSystem](#eventsourcingsystem-api)
7. [Stripe Integration](#stripe-integration)
8. [Production Deployment](#production-deployment)
9. [npm / JavaScript Ecosystem](#npm--javascript-ecosystem)
10. [Testing](#testing)
11. [Contributing](#contributing)
12. [Security](#security)
13. [License](#license)

---

## Overview

`blackroad-event-sourcing` is a lightweight, zero-dependency Python library that brings **Event Sourcing** and **CQRS** patterns to any application. Events are stored in an append-only log (SQLite by default, swappable for Postgres or any persistence layer), and read-models are rebuilt deterministically from that log.

It is the backbone of the BlackRoad OS payment and order platform, processing millions of domain events — including real-time **Stripe webhook events** — reliably and reproducibly.

---

## Features

| Feature | Description |
|---|---|
| 📝 **Event Store** | Append-only SQLite event log with global position tracking and indexed queries |
| 🔄 **CQRS** | Command / Query separation with a typed command bus and full audit log |
| 📊 **Projections** | Incrementally rebuild read-models from any position in the event stream |
| 📸 **Snapshots** | Point-in-time aggregate snapshots for fast reconstruction without full replay |
| 🏗️ **Aggregate Reconstruction** | Replay from snapshot + delta events in a single call |
| 🔍 **Event Queries** | Load events by aggregate ID, aggregate type, or global stream position |
| 💳 **Stripe-Ready** | First-class patterns for handling Stripe webhook events idempotently |
| 🧪 **Fully Tested** | 21 unit tests across every layer; run in CI on Python 3.10, 3.11, and 3.12 |

---

## Installation

### PyPI (recommended)

```bash
pip install blackroad-event-sourcing
```

### From Source

```bash
git clone https://github.com/BlackRoad-OS/blackroad-event-sourcing.git
cd blackroad-event-sourcing
pip install -e .
```

### Requirements

- Python **3.10+**
- No mandatory runtime dependencies (SQLite is included with Python)

---

## Quick Start

```python
from event_sourcing import EventSourcingSystem, Event, Projection

# In-memory store for development; pass a file path for persistence
system = EventSourcingSystem(db_path=":memory:")

# ── 1. Append events ──────────────────────────────────────────────────────────
evt = Event.create(
    aggregate_id="order-1",
    aggregate_type="Order",
    event_type="OrderCreated",
    payload={"total": 99.99, "currency": "usd"},
    version=1,
)
system.store.append("order-1", [evt])

# ── 2. Register and rebuild a projection ─────────────────────────────────────
proj = Projection("order_totals")
proj.handlers["OrderCreated"] = (
    lambda state, evt: state.update({evt.aggregate_id: evt.payload["total"]})
)
system.projections.register(proj)
system.rebuild_projection("order_totals")

total = system.query_projection("order_totals", "order-1")  # → 99.99

# ── 3. Dispatch a command ─────────────────────────────────────────────────────
def handle_create_order(cmd, store):
    evt = Event.create(
        cmd.payload["id"], "Order", "OrderCreated", cmd.payload, version=1
    )
    store.append(cmd.payload["id"], [evt])
    return {"id": cmd.payload["id"]}

system.command_bus.register("CreateOrder", handle_create_order)
result = system.dispatch_command("CreateOrder", {"id": "order-2", "total": 50.0})
# → {"status": "ok", "result": {"id": "order-2"}}
```

---

## Core Concepts

### Event

An **Event** is an immutable record of something that happened in the domain. Events carry a `payload`, belong to an `aggregate_id`, and have a monotonically increasing `version` within that aggregate.

```python
Event(
    id="uuid",
    aggregate_id="order-1",
    aggregate_type="Order",
    event_type="OrderShipped",
    payload={"tracking": "1Z999AA1"},
    version=3,
    timestamp="2024-01-15T12:00:00",
    caused_by="cmd-uuid",   # optional: originating command ID
    metadata={},            # optional: arbitrary key/value pairs
)
```

### Aggregate

An **Aggregate** is a consistency boundary. It accumulates state by applying events. Override `apply()` in subclasses for typed state transitions.

```python
from event_sourcing import Aggregate, Event

class OrderAggregate(Aggregate):
    def apply(self, event: Event):
        super().apply(event)
        if event.event_type == "OrderShipped":
            self.state["shipped"] = True
```

### Event Store

The **EventStore** is the single source of truth — an append-only log backed by SQLite. It supports:

- `append(aggregate_id, events)` — write events
- `load(aggregate_id, from_version)` — read events for one aggregate
- `load_all(aggregate_type, after_position)` — read events by type
- `load_all_events(after_position)` — read the global stream
- `create_snapshot / load_snapshot` — snapshot management
- `reconstruct(aggregate_id, aggregate_type)` — rebuild from snapshot + delta

### Command Bus (CQRS)

The **CommandBus** separates write concerns from read concerns. Every dispatched command is logged to `command_log` with its status (`pending → ok | error`), providing a full audit trail.

```python
system.command_bus.register("ShipOrder", handle_ship_order)
result = system.dispatch_command("ShipOrder", {"order_id": "order-1"})
```

### Projections

**Projections** subscribe to event types and maintain a denormalised read-model. They are rebuilt deterministically from the event stream, making them safe to wipe and recreate at any time.

```python
proj = Projection("active_subscriptions")
proj.handlers["SubscriptionCreated"] = lambda state, evt: state.update(
    {evt.aggregate_id: evt.payload}
)
proj.handlers["SubscriptionCancelled"] = lambda state, evt: state.pop(
    evt.aggregate_id, None
)
system.projections.register(proj)
system.rebuild_projection("active_subscriptions")
```

### Snapshots

A **Snapshot** captures the collapsed state of an aggregate at a specific version so that replay only needs to process events *after* the snapshot.

```python
system.store.create_snapshot("order-1")   # store snapshot
agg = system.store.reconstruct("order-1", "Order")  # snapshot + delta
```

---

## API Reference

### Event API

| Method / Field | Type | Description |
|---|---|---|
| `Event.create(aggregate_id, aggregate_type, event_type, payload, version, caused_by, **metadata)` | `classmethod → Event` | Factory; auto-assigns `id` and `timestamp` |
| `event.to_dict()` | `→ Dict` | Serialize to plain dictionary |
| `event.id` | `str` | UUID v4 |
| `event.aggregate_id` | `str` | Owner aggregate |
| `event.event_type` | `str` | Domain event name (e.g. `"OrderCreated"`) |
| `event.version` | `int` | Sequence within aggregate |
| `event.caused_by` | `str \| None` | ID of the command that triggered this event |
| `event.metadata` | `Dict` | Arbitrary contextual data |

### Aggregate API

| Method / Field | Type | Description |
|---|---|---|
| `agg.apply(event)` | `→ None` | Apply an event to aggregate state |
| `agg.raise_event(event_type, payload, caused_by)` | `→ Event` | Increment version, create + apply event |
| `agg.id` | `str` | Aggregate identifier |
| `agg.version` | `int` | Current version |
| `agg.state` | `Dict` | Current collapsed state |

### EventStore API

| Method | Returns | Description |
|---|---|---|
| `EventStore(db_path)` | `EventStore` | `":memory:"` or file path |
| `store.append(aggregate_id, events)` | `List[int]` | Append events; returns DB positions |
| `store.load(aggregate_id, from_version)` | `List[Event]` | Events for one aggregate |
| `store.load_all(aggregate_type, after_position)` | `List[Event]` | Events by aggregate type |
| `store.load_all_events(after_position)` | `List[Event]` | Full global stream |
| `store.get_position()` | `int` | Latest global position |
| `store.create_snapshot(aggregate_id)` | `Snapshot \| None` | Create and persist snapshot |
| `store.load_snapshot(aggregate_id)` | `Snapshot \| None` | Load latest snapshot |
| `store.reconstruct(aggregate_id, aggregate_type)` | `Aggregate` | Rebuild from snapshot + delta |

### CommandBus API

| Method | Returns | Description |
|---|---|---|
| `CommandBus(event_store)` | `CommandBus` | Constructor |
| `bus.register(cmd_type, handler)` | `None` | Register `handler(cmd, store) → Dict` |
| `bus.dispatch(cmd_type, payload, issued_by)` | `Dict` | Dispatch command; returns `{"status": "ok" \| "error", ...}` |

### ProjectionManager API

| Method | Returns | Description |
|---|---|---|
| `ProjectionManager(event_store)` | `ProjectionManager` | Constructor |
| `mgr.register(projection)` | `None` | Register projection, restoring persisted state |
| `mgr.rebuild_projection(name)` | `int` | Full replay from position 0; returns event count |
| `mgr.advance(name)` | `int` | Incremental update from last position |
| `mgr.advance_all()` | `Dict[str, int]` | Advance all registered projections |
| `mgr.query_projection(name, key)` | `Any` | Read state or a single key |

### EventSourcingSystem API

The `EventSourcingSystem` is the high-level facade that wires `EventStore`, `CommandBus`, and `ProjectionManager` together.

| Method | Returns | Description |
|---|---|---|
| `EventSourcingSystem(db_path)` | `EventSourcingSystem` | Convenience constructor |
| `system.dispatch_command(cmd_type, payload, issued_by)` | `Dict` | Delegates to `CommandBus` |
| `system.rebuild_projection(name)` | `int` | Delegates to `ProjectionManager` |
| `system.query_projection(name, key)` | `Any` | Delegates to `ProjectionManager` |
| `system.get_aggregate_history(aggregate_id)` | `List[Dict]` | All events for an aggregate as dicts |
| `system.statistics()` | `Dict` | Total events, events by type, latest position |

---

## Stripe Integration

`blackroad-event-sourcing` is purpose-built to handle **Stripe webhook events** idempotently. Every Stripe event becomes a domain event in the append-only store, giving you a full audit log and deterministic projections for billing state.

```python
import stripe
from event_sourcing import EventSourcingSystem, Event, Projection

system = EventSourcingSystem(db_path="blackroad.db")

# ── Projection: active subscriptions read-model ───────────────────────────────
sub_proj = Projection("active_subscriptions")
sub_proj.handlers["customer.subscription.created"] = lambda state, evt: state.update(
    {evt.aggregate_id: {"status": "active", **evt.payload}}
)
sub_proj.handlers["customer.subscription.deleted"] = lambda state, evt: state.pop(
    evt.aggregate_id, None
)
system.projections.register(sub_proj)

# ── Projection: payment ledger ────────────────────────────────────────────────
ledger_proj = Projection("payment_ledger")
ledger_proj.handlers["payment_intent.succeeded"] = lambda state, evt: state.update(
    {evt.aggregate_id: evt.payload.get("amount", 0)}
)
system.projections.register(ledger_proj)

# ── Stripe webhook handler (e.g. Flask / FastAPI route) ───────────────────────
def handle_stripe_webhook(raw_body: bytes, sig_header: str, webhook_secret: str):
    stripe_event = stripe.Webhook.construct_event(raw_body, sig_header, webhook_secret)

    # Map Stripe event → domain Event
    evt = Event.create(
        aggregate_id=stripe_event["data"]["object"]["id"],
        aggregate_type="StripeWebhook",
        event_type=stripe_event["type"],          # e.g. "payment_intent.succeeded"
        payload=stripe_event["data"]["object"],
        version=1,
        caused_by=stripe_event["id"],             # Stripe event ID for idempotency
    )
    system.store.append(evt.aggregate_id, [evt])
    system.projections.advance_all()

    return {"received": True}

# ── Query billing state ───────────────────────────────────────────────────────
active_subs = system.query_projection("active_subscriptions")
total_paid  = sum(system.query_projection("payment_ledger").values())
```

> **Idempotency note:** Use the Stripe event `id` as `caused_by`. Store the Stripe event ID in `metadata` and check for duplicates before appending if strict exactly-once delivery is required.

---

## Production Deployment

### Persistent Storage

Pass a file path to `EventSourcingSystem` to persist events on disk:

```python
system = EventSourcingSystem(db_path="/var/data/blackroad.db")
```

For high-throughput workloads, the `EventStore` connection can be replaced with a PostgreSQL adapter by overriding `_init_db` and the SQL statements — the public API is identical.

### Database Indexes

The event store ships with two indexes out of the box:

```sql
CREATE INDEX idx_aggregate ON events(aggregate_id, version);
CREATE INDEX idx_type      ON events(aggregate_type);
```

For large deployments, add a position-based index if you query the global stream heavily:

```sql
CREATE INDEX idx_position ON events(position);
```

### Snapshot Strategy

Take snapshots periodically to keep reconstruction fast:

```python
# Snapshot every 100 events
if system.store.get_position() % 100 == 0:
    system.store.create_snapshot(aggregate_id)
```

### Environment Variables (recommended)

| Variable | Purpose | Example |
|---|---|---|
| `BLACKROAD_DB_PATH` | Path to SQLite database | `/var/data/blackroad.db` |
| `STRIPE_WEBHOOK_SECRET` | Stripe signing secret | `whsec_…` |
| `LOG_LEVEL` | Python logging level | `INFO` |

---

## npm / JavaScript Ecosystem

The **BlackRoad OS** platform ships a companion JavaScript/TypeScript package for front-end and Node.js consumers:

```bash
npm install @blackroad/event-client
```

The npm package provides a typed client for reading projections and dispatching commands over HTTP, targeting a Python `blackroad-event-sourcing` back-end. See the [@blackroad/event-client](https://www.npmjs.com/package/@blackroad/event-client) package for full documentation.

---

## Testing

```bash
# Install test dependencies
pip install pytest pytest-cov

# Run all tests with coverage
PYTHONPATH=. pytest tests/ -v --cov=event_sourcing --cov-report=term-missing
```

The test suite covers:

| Class | Tests |
|---|---|
| `TestEvent` | create, serialization, caused_by |
| `TestAggregate` | apply, raise_event |
| `TestEventStore` | append/load, versioned load, snapshot lifecycle, reconstruction |
| `TestCommandBus` | dispatch with handler, no handler |
| `TestProjection` | handler dispatch, rebuild, keyed query |
| `TestSystem` | statistics, aggregate history |

CI runs against **Python 3.10, 3.11, and 3.12** on every push and pull request.

---

## Contributing

1. Fork the repository and create a feature branch.
2. Write tests for every new behavior — keep coverage above 90 %.
3. Run `PYTHONPATH=. pytest tests/ -v` and confirm all tests pass.
4. Open a pull request against `main` with a clear description.

Please follow the existing code style (standard Python, dataclasses, type hints).

---

## Security

If you discover a security vulnerability, **do not open a public issue**. Email [security@blackroad.dev](mailto:security@blackroad.dev) with a description and reproduction steps. We follow a 90-day responsible-disclosure policy.

---

## License

Proprietary — © BlackRoad OS, Inc. All rights reserved.

Unauthorised copying, distribution, or modification of this software is strictly prohibited. See [LICENSE](./LICENSE) for the full terms.
