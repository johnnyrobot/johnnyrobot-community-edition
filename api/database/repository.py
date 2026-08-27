"""
Owner-scoped access to every Student-owned record.

This is the only layer routers, services, and the voice agent call. Every
method takes the Student identity as its first argument and injects the
ownership filter itself, so a new endpoint cannot omit it — there is no method
that reads or writes without an owner.

`student_id` is always the PocketBase record id (the PocketBase identity contract).
"""
import logging
from typing import Any, Optional

from api.database.store import DocumentStore, get_store

logger = logging.getLogger(__name__)

MATERIALS = "course_materials"
CANVAS_TOKENS = "canvas_tokens"
CANVAS_DATA = "canvas_data"
SESSIONS = "sessions"
STUDENTS = "users"
GRAPH_MANIFESTS = "graph_build_manifests"


class Repository:
    def __init__(self, store: DocumentStore):
        self._store = store

    # -- Student -------------------------------------------------------------

    async def get_student(self, student_id: str) -> Optional[dict[str, Any]]:
        return await self._store.get(STUDENTS, student_id)

    async def update_student(self, student_id: str, data: dict[str, Any]) -> None:
        await self._store.set(STUDENTS, student_id, self._without_owner(data), merge=True)

    # -- Course Materials ----------------------------------------------------

    async def create_material(self, student_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return await self._store.create(MATERIALS, self._owned(student_id, data))

    async def get_material(self, student_id: str, material_id: str) -> Optional[dict[str, Any]]:
        matches = await self._store.query(MATERIALS, {"id": material_id, "student": student_id})
        return matches[0] if matches else None

    async def list_materials(self, student_id: str) -> list[dict[str, Any]]:
        return await self._store.query(MATERIALS, {"student": student_id}, order_by="created")

    async def find_material_by_source(
        self, student_id: str, source_identity: str
    ) -> Optional[dict[str, Any]]:
        if not source_identity:
            return None
        matches = await self._store.query(
            MATERIALS, {"student": student_id, "source_identity": source_identity}
        )
        return matches[0] if matches else None

    async def update_material(
        self, student_id: str, material_id: str, data: dict[str, Any]
    ) -> bool:
        if await self.get_material(student_id, material_id) is None:
            return False
        await self._store.update(MATERIALS, material_id, self._without_owner(data))
        return True

    async def delete_material(self, student_id: str, material_id: str) -> bool:
        if await self.get_material(student_id, material_id) is None:
            return False
        await self._store.delete(MATERIALS, material_id)
        return True

    # -- Graph build provenance ----------------------------------------------

    async def create_graph_manifest(
        self, student_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Record one graph build.

        Owner-scoped like everything else here, even though a manifest holds no
        content: a Deployment Operator reading build history is reading which
        of a Student's materials were built and when, which is still theirs.
        """
        return await self._store.create(GRAPH_MANIFESTS, self._owned(student_id, data))

    async def list_graph_manifests(
        self, student_id: str, material_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Build history, newest first. Two builds of one Source Copy that
        disagree show up here as differing counts against identical stage
        digests -- the graph-build determinism contract's Drifted outcome, made visible rather than silent.
        """
        where: dict[str, Any] = {"student": student_id}
        if material_id:
            where["material"] = material_id
        return await self._store.query(
            GRAPH_MANIFESTS, where, order_by="created", descending=True
        )

    # -- Material Source: Canvas ---------------------------------------------

    async def get_canvas_token(self, student_id: str) -> Optional[dict[str, Any]]:
        matches = await self._store.query(CANVAS_TOKENS, {"student": student_id})
        return matches[0] if matches else None

    async def upsert_canvas_token(self, student_id: str, data: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_canvas_token(student_id)
        if existing is None:
            return await self._store.create(CANVAS_TOKENS, self._owned(student_id, data))
        await self._store.update(CANVAS_TOKENS, existing["id"], self._without_owner(data))
        return {**existing, **self._without_owner(data)}

    async def mark_canvas_source_disconnected(self, student_id: str) -> None:
        """Disconnect rather than destroy, so Disconnected Source can exist (the source-identity contract)."""
        existing = await self.get_canvas_token(student_id)
        if existing is None:
            return
        await self._store.update(
            CANVAS_TOKENS,
            existing["id"],
            {"disconnected": True, "api_token_ciphertext": "", "key_version": 0},
        )

    async def upsert_canvas_record(
        self, student_id: str, data_type: str, canvas_id: str, data: dict[str, Any]
    ) -> None:
        key = {"student": student_id, "data_type": data_type, "canvas_id": canvas_id}
        matches = await self._store.query(CANVAS_DATA, key)
        payload = {**self._without_owner(data), **key}
        if matches:
            await self._store.update(CANVAS_DATA, matches[0]["id"], payload)
        else:
            await self._store.create(CANVAS_DATA, payload)

    async def list_canvas_records(
        self,
        student_id: str,
        *,
        data_type: Optional[str] = None,
        course_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where: dict[str, Any] = {"student": student_id}
        if data_type:
            where["data_type"] = data_type
        if course_id:
            where["course_id"] = course_id
        return await self._store.query(CANVAS_DATA, where, order_by="created", descending=True)

    async def delete_canvas_records(self, student_id: str) -> int:
        return await self._store.delete_where(CANVAS_DATA, {"student": student_id})

    # -- Tutor Sessions ------------------------------------------------------

    async def create_tutor_session(
        self, student_id: str, room_name: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._store.create(
            SESSIONS, self._owned(student_id, {**data, "room_name": room_name})
        )

    async def get_tutor_session(
        self, student_id: str, room_name: str
    ) -> Optional[dict[str, Any]]:
        matches = await self._store.query(
            SESSIONS, {"student": student_id, "room_name": room_name}
        )
        return matches[0] if matches else None

    async def update_tutor_session(
        self, student_id: str, room_name: str, data: dict[str, Any]
    ) -> bool:
        existing = await self.get_tutor_session(student_id, room_name)
        if existing is None:
            return False
        await self._store.update(SESSIONS, existing["id"], self._without_owner(data))
        return True

    async def list_tutor_sessions(self, student_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self._store.query(
            SESSIONS, {"student": student_id}, order_by="start_time", descending=True, limit=limit
        )

    async def end_open_tutor_session(self, student_id: str, data: dict[str, Any]) -> bool:
        """Close the caller's most recent Tutor Session with no `end_time`.

        A "room" is LiveKit plumbing, not a product concept -- the caller
        never names which session to end. An older open session (a stale tab,
        a crashed browser) is left alone rather than closed as a side effect.
        """
        matches = await self._store.query(
            SESSIONS,
            {"student": student_id, "end_time": ""},
            order_by="start_time",
            descending=True,
            limit=1,
        )
        if not matches:
            return False
        await self._store.update(SESSIONS, matches[0]["id"], self._without_owner(data))
        return True

    # -- plumbing ------------------------------------------------------------

    @staticmethod
    def _owned(student_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Stamp the owner last, so a payload cannot name a different one."""
        return {**data, "student": student_id}

    @staticmethod
    def _without_owner(data: dict[str, Any]) -> dict[str, Any]:
        """Ownership is never changed by an update."""
        return {key: value for key, value in data.items() if key != "student"}


def get_repository() -> Repository:
    """Return a repository over the currently installed store."""
    return Repository(get_store())
