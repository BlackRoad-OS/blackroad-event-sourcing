"""
BlackRoad Event Sourcing - CQRS framework with projections and snapshots
"""
from __future__ import annotations
import json
import uuid
import sqlite3
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Type
from datetime import datetime
from copy import deepcopy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------

@dataclass
class Event:
    id: str
    aggregate_id: str
    aggregate_type: str
    event_type: str
    payload: Dict[str, Any]
    version: int
    timestamp: str
    caused_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        aggregate_id: str,
        aggregate_type: str,
        event_type: str,
        payload: Dict,
        version: int,
        caused_by: Optional[str] = None,
        **metadata,
    ) -> "Event":
        return cls(
            id=str(uuid.uuid4()),
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            event_type=event_type,
            payload=payload,
            version=version,
            timestamp=datetime.utcnow().isoformat(),
            caused_by=caused_by,
            metadata=metadata,
        )

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type,
            "event_type": self.event_type,
            "payload": self.payload,
            "version": self.version,
            "timestamp": self.timestamp,
            "caused_by": self.caused_by,
            "metadata": self.metadata,
        }


@dataclass
class Aggregate:
    id: str
    type: str
    version: int = 0
    state: Dict[str, Any] = field(default_factory=dict)

    def apply(self, event: Event):
        """Override in subclasses to apply events to state."""
        self.version = event.version
        self.state.update(event.payload)

    def raise_event(
        self,
        event_type: str,
        payload: Dict,
        caused_by: Optional[str] = None,
    ) -> Event:
        self.version += 1
        evt = Event.create(
            aggregate_id=self.id,
            aggregate_type=self.type,
            event_type=event_type,
            payload=payload,
            version=self.version,
            caused_by=caused_by,
        )
        self.apply(evt)
        return evt


@dataclass
class Projection:
    name: str
    handlers: Dict[str, Callable] = field(default_factory=dict)
    last_position: int = 0
    state: Dict[str, Any] = field(default_factory=dict)

    def handle(self, event: Event) -> bool:
        handler = self.handlers.get(event.event_type)
        if handler:
            try:
                handler(self.state, event)
                return True
            except Exception as exc:
                logger.error("Projection %s handler error: %s", self.name, exc)
                return False
        return False


@dataclass
class Snapshot:
    aggregate_id: str
    version: int
    state: Dict[str, Any]
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict:
        return {
            "aggregate_id": self.aggregate_id,
            "version": self.version,
            "state": self.state,
            "created_at": self.created_at,
        }


@dataclass
class Command:
    type: str
    payload: Dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    issued_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    issued_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Event Store (SQLite, append-only)
# ---------------------------------------------------------------------------

class EventStore:
    """Append-only SQLite event store."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()
        self._position_counter = 0

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                position     INTEGER PRIMARY KEY AUTOINCREMENT,
                id           TEXT UNIQUE NOT NULL,
                aggregate_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                event_type   TEXT NOT NULL,
                payload      TEXT NOT NULL,
                version      INTEGER NOT NULL,
                timestamp    TEXT NOT NULL,
                caused_by    TEXT,
                metadata     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_aggregate ON events(aggregate_id, version);
            CREATE INDEX IF NOT EXISTS idx_type ON events(aggregate_type);

            CREATE TABLE IF NOT EXISTS snapshots (
                aggregate_id TEXT PRIMARY KEY,
                version      INTEGER NOT NULL,
                state        TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projections (
                name          TEXT PRIMARY KEY,
                last_position INTEGER NOT NULL DEFAULT 0,
                state         TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS command_log (
                id         TEXT PRIMARY KEY,
                type       TEXT NOT NULL,
                payload    TEXT NOT NULL,
                status     TEXT NOT NULL,
                issued_at  TEXT NOT NULL,
                handled_at TEXT
            );
        """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Core append/load
    # ------------------------------------------------------------------

    def append(self, aggregate_id: str, events: List[Event]) -> List[int]:
        """Append events for an aggregate and return their positions."""
        positions = []
        for evt in events:
            cur = self.conn.execute(
                """INSERT INTO events
                   (id, aggregate_id, aggregate_type, event_type, payload,
                    version, timestamp, caused_by, metadata)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    evt.id,
                    evt.aggregate_id,
                    evt.aggregate_type,
                    evt.event_type,
                    json.dumps(evt.payload),
                    evt.version,
                    evt.timestamp,
                    evt.caused_by,
                    json.dumps(evt.metadata),
                ),
            )
            positions.append(cur.lastrowid)
        self.conn.commit()
        logger.debug("Appended %d events for aggregate %s", len(events), aggregate_id)
        return positions

    def load(self, aggregate_id: str, from_version: int = 0) -> List[Event]:
        """Load all events for an aggregate from a given version."""
        rows = self.conn.execute(
            "SELECT id, aggregate_id, aggregate_type, event_type, payload, version, timestamp, caused_by, metadata "
            "FROM events WHERE aggregate_id=? AND version>? ORDER BY version ASC",
            (aggregate_id, from_version),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def load_all(self, aggregate_type: str, after_position: int = 0) -> List[Event]:
        """Load all events of a given aggregate type after a position."""
        rows = self.conn.execute(
            "SELECT id, aggregate_id, aggregate_type, event_type, payload, version, timestamp, caused_by, metadata "
            "FROM events WHERE aggregate_type=? AND position>? ORDER BY position ASC",
            (aggregate_type, after_position),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def load_all_events(self, after_position: int = 0) -> List[Event]:
        """Load all events in the store after a position."""
        rows = self.conn.execute(
            "SELECT id, aggregate_id, aggregate_type, event_type, payload, version, timestamp, caused_by, metadata "
            "FROM events WHERE position>? ORDER BY position ASC",
            (after_position,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_position(self) -> int:
        """Return the latest global event position."""
        row = self.conn.execute("SELECT MAX(position) FROM events").fetchone()
        return row[0] or 0

    def _row_to_event(self, row) -> Event:
        return Event(
            id=row[0],
            aggregate_id=row[1],
            aggregate_type=row[2],
            event_type=row[3],
            payload=json.loads(row[4]),
            version=row[5],
            timestamp=row[6],
            caused_by=row[7],
            metadata=json.loads(row[8]) if row[8] else {},
        )

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def create_snapshot(self, aggregate_id: str) -> Optional[Snapshot]:
        """Create a snapshot for an aggregate from current events."""
        events = self.load(aggregate_id)
        if not events:
            return None
        state: Dict = {}
        version = 0
        for evt in events:
            state.update(evt.payload)
            version = evt.version
        snap = Snapshot(aggregate_id=aggregate_id, version=version, state=state)
        self.conn.execute(
            "INSERT OR REPLACE INTO snapshots (aggregate_id, version, state, created_at) VALUES (?,?,?,?)",
            (snap.aggregate_id, snap.version, json.dumps(snap.state), snap.created_at),
        )
        self.conn.commit()
        logger.info("Created snapshot for %s at version %d", aggregate_id, version)
        return snap

    def load_snapshot(self, aggregate_id: str) -> Optional[Snapshot]:
        """Load the latest snapshot for an aggregate."""
        row = self.conn.execute(
            "SELECT aggregate_id, version, state, created_at FROM snapshots WHERE aggregate_id=?",
            (aggregate_id,),
        ).fetchone()
        if not row:
            return None
        return Snapshot(
            aggregate_id=row[0],
            version=row[1],
            state=json.loads(row[2]),
            created_at=row[3],
        )

    # ------------------------------------------------------------------
    # Aggregate reconstruction
    # ------------------------------------------------------------------

    def reconstruct(self, aggregate_id: str, aggregate_type: str) -> Aggregate:
        """Reconstruct an aggregate from snapshot + events."""
        snap = self.load_snapshot(aggregate_id)
        from_version = 0
        agg = Aggregate(id=aggregate_id, type=aggregate_type)
        if snap:
            agg.version = snap.version
            agg.state = deepcopy(snap.state)
            from_version = snap.version

        events = self.load(aggregate_id, from_version=from_version)
        for evt in events:
            agg.apply(evt)
        return agg


# ---------------------------------------------------------------------------
# CQRS Bus
# ---------------------------------------------------------------------------

class CommandBus:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
        self._handlers: Dict[str, Callable] = {}

    def register(self, cmd_type: str, handler: Callable):
        self._handlers[cmd_type] = handler
        logger.debug("Registered handler for command: %s", cmd_type)

    def dispatch(self, cmd_type: str, payload: Dict, issued_by: Optional[str] = None) -> Dict:
        cmd = Command(type=cmd_type, payload=payload, issued_by=issued_by)
        self.event_store.conn.execute(
            "INSERT INTO command_log (id, type, payload, status, issued_at) VALUES (?,?,?,?,?)",
            (cmd.id, cmd.type, json.dumps(cmd.payload), "pending", cmd.issued_at),
        )
        self.event_store.conn.commit()

        handler = self._handlers.get(cmd_type)
        if not handler:
            self.event_store.conn.execute(
                "UPDATE command_log SET status=? WHERE id=?", ("no_handler", cmd.id)
            )
            self.event_store.conn.commit()
            return {"status": "error", "message": f"No handler for command '{cmd_type}'"}

        try:
            result = handler(cmd, self.event_store)
            self.event_store.conn.execute(
                "UPDATE command_log SET status=?, handled_at=? WHERE id=?",
                ("ok", datetime.utcnow().isoformat(), cmd.id),
            )
            self.event_store.conn.commit()
            return {"status": "ok", "result": result}
        except Exception as exc:
            self.event_store.conn.execute(
                "UPDATE command_log SET status=? WHERE id=?", (f"error:{exc}", cmd.id)
            )
            self.event_store.conn.commit()
            return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Projection Manager
# ---------------------------------------------------------------------------

class ProjectionManager:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store
        self._projections: Dict[str, Projection] = {}

    def register(self, projection: Projection):
        self._projections[projection.name] = projection
        row = self.event_store.conn.execute(
            "SELECT last_position, state FROM projections WHERE name=?", (projection.name,)
        ).fetchone()
        if row:
            projection.last_position = row[0]
            projection.state = json.loads(row[1])
        else:
            self.event_store.conn.execute(
                "INSERT INTO projections (name, last_position, state) VALUES (?,?,?)",
                (projection.name, 0, "{}"),
            )
            self.event_store.conn.commit()

    def _save_projection(self, projection: Projection):
        self.event_store.conn.execute(
            "INSERT OR REPLACE INTO projections (name, last_position, state) VALUES (?,?,?)",
            (projection.name, projection.last_position, json.dumps(projection.state)),
        )
        self.event_store.conn.commit()

    def rebuild_projection(self, name: str) -> int:
        """Rebuild a projection from all events."""
        proj = self._projections.get(name)
        if not proj:
            raise ValueError(f"Projection '{name}' not registered")
        proj.state = {}
        proj.last_position = 0
        events = self.event_store.load_all_events()
        count = 0
        for evt in events:
            proj.handle(evt)
            count += 1
        proj.last_position = self.event_store.get_position()
        self._save_projection(proj)
        logger.info("Rebuilt projection '%s': processed %d events", name, count)
        return count

    def advance(self, name: str) -> int:
        """Advance a projection by processing new events."""
        proj = self._projections.get(name)
        if not proj:
            raise ValueError(f"Projection '{name}' not registered")
        events = self.event_store.load_all_events(after_position=proj.last_position)
        count = 0
        for evt in events:
            proj.handle(evt)
            count += 1
        if count:
            proj.last_position = self.event_store.get_position()
            self._save_projection(proj)
        return count

    def query_projection(self, name: str, key: Optional[str] = None) -> Any:
        """Query a projection's current state."""
        proj = self._projections.get(name)
        if not proj:
            raise ValueError(f"Projection '{name}' not registered")
        if key is None:
            return proj.state
        return proj.state.get(key)

    def advance_all(self) -> Dict[str, int]:
        return {name: self.advance(name) for name in self._projections}


# ---------------------------------------------------------------------------
# High-level EventSourcingSystem facade
# ---------------------------------------------------------------------------

class EventSourcingSystem:
    def __init__(self, db_path: str = ":memory:"):
        self.store = EventStore(db_path)
        self.command_bus = CommandBus(self.store)
        self.projections = ProjectionManager(self.store)

    def dispatch_command(self, cmd_type: str, payload: Dict, issued_by: Optional[str] = None) -> Dict:
        return self.command_bus.dispatch(cmd_type, payload, issued_by)

    def rebuild_projection(self, name: str) -> int:
        return self.projections.rebuild_projection(name)

    def query_projection(self, name: str, key: Optional[str] = None) -> Any:
        return self.projections.query_projection(name, key)

    def get_aggregate_history(self, aggregate_id: str) -> List[Dict]:
        events = self.store.load(aggregate_id)
        return [e.to_dict() for e in events]

    def statistics(self) -> Dict:
        total = self.store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        types = self.store.conn.execute(
            "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"
        ).fetchall()
        return {
            "total_events": total,
            "by_type": {r[0]: r[1] for r in types},
            "latest_position": self.store.get_position(),
        }
