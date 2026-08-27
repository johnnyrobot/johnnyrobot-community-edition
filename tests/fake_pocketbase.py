"""
An in-process PocketBase, good enough to be the only thing the tests fake.

Everything above the adapter runs for real in these tests: routers,
dependencies, the repository. Only the socket is replaced. The fake therefore
has to be honest about the things the application actually depends on --
definitive rejection versus outage, superuser-only rules, equality filters,
The partial unique index on (student, source_identity), and the
`created`/`updated` autodate fields every record carries.

It is deliberately narrow: it answers the endpoints the adapter calls and
nothing else. tests/test_pocketbase_contract.py guards it against drift.
"""
import itertools
import json
import re
import string
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qs

import httpx

PB_ID_ALPHABET = string.ascii_lowercase + string.digits

_counter = itertools.count(1)


def new_id(seed: Optional[int] = None) -> str:
    """A 15-character PocketBase-shaped record id."""
    value = seed if seed is not None else next(_counter)
    return f"{value:015d}".replace("0", "a", 1)[:15].ljust(15, "z")


def _now() -> str:
    """PocketBase's default autodate serialization: `YYYY-MM-DD HH:MM:SS.mmmZ`
    (space separator, millisecond precision, literal 'Z' -- not Python's
    `isoformat()`, which uses 'T' and either six-digit microseconds or none).
    Real PocketBase's `types.DateTime.MarshalJSON` emits exactly this shape
, and the API-facing mapping this fake exists to exercise must be
    proven against that shape, not a more convenient one.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


# Equality conjunctions are the only filter shape the repository emits.
_CLAUSE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


class FakePocketBase:
    def __init__(self):
        self._collections: dict[str, list[dict[str, Any]]] = {}
        self._students: dict[str, dict[str, Any]] = {}
        self._tokens: dict[str, str] = {}
        self._expired: set[str] = set()
        self._failure: Optional[str] = None
        self._superuser_token = "superuser-token-for-test"

    # -- test-facing surface -------------------------------------------------

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def add_student(self, email: str, password: str, **fields) -> str:
        student_id = new_id()
        record = {"id": student_id, "email": email, "name": email.split("@")[0], **fields}
        self._students[student_id] = {"record": record, "password": password}
        self._collections.setdefault("users", []).append(record)
        self._tokens[f"token-{student_id}"] = student_id
        return student_id

    def token_for(self, student_id: str) -> str:
        return f"token-{student_id}"

    def expire(self, token: str) -> None:
        self._expired.add(token)

    def superuser_token_for_test(self) -> str:
        return self._superuser_token

    def records(self, collection: str) -> list[dict[str, Any]]:
        return [dict(record) for record in self._collections.get(collection, [])]

    def fail_with(self, mode: Optional[str]) -> None:
        self._failure = mode

    # -- request handling ----------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self._failure == "timeout":
            raise httpx.ReadTimeout("injected timeout", request=request)
        if self._failure == "refused":
            raise httpx.ConnectError("injected connection refusal", request=request)
        if self._failure == "server-error":
            return httpx.Response(500, text="injected provider failure", request=request)

        path = request.url.path
        if path.endswith("/_superusers/auth-with-password"):
            return self._superuser_login(request)
        if path.endswith("/users/auth-with-password"):
            return self._student_login(request)
        if path.endswith("/users/auth-refresh"):
            return self._auth_refresh(request)
        if path == "/api/health":
            return httpx.Response(200, json={"code": 200}, request=request)
        if "/api/collections/" in path:
            return self._records(request)
        return httpx.Response(404, json={"message": "not found"}, request=request)

    def _superuser_login(self, request):
        return httpx.Response(
            200, json={"token": self._superuser_token, "record": {"id": "operator"}}, request=request
        )

    def _student_login(self, request):
        body = json.loads(request.content or b"{}")
        for student_id, entry in self._students.items():
            if entry["record"]["email"] == body.get("identity"):
                if entry["password"] == body.get("password"):
                    return httpx.Response(
                        200,
                        json={"token": self.token_for(student_id), "record": entry["record"]},
                        request=request,
                    )
                break
        return httpx.Response(400, json={"message": "Failed to authenticate."}, request=request)

    def _auth_refresh(self, request):
        token = request.headers.get("authorization", "")
        if token in self._expired or token not in self._tokens:
            return httpx.Response(401, json={"message": "The request requires valid record authorization token."}, request=request)
        student_id = self._tokens[token]
        return httpx.Response(
            200, json={"token": token, "record": self._students[student_id]["record"]}, request=request
        )

    def _records(self, request):
        # Every business collection is superuser-only.
        if request.headers.get("authorization") != self._superuser_token:
            return httpx.Response(403, json={"message": "Only superusers can perform this action."}, request=request)

        parts = request.url.path.split("/")
        collection = parts[parts.index("collections") + 1]
        rows = self._collections.setdefault(collection, [])
        record_id = parts[-1] if parts[-1] != "records" else None

        if request.method == "GET" and record_id is None:
            return self._list(request, rows)
        if request.method == "GET":
            found = self._find(rows, record_id)
            if found is None:
                return httpx.Response(404, json={"message": "not found"}, request=request)
            return httpx.Response(200, json=found, request=request)
        if request.method == "POST":
            return self._create(request, collection, rows)
        if request.method == "PATCH":
            found = self._find(rows, record_id)
            if found is None:
                return httpx.Response(404, json={"message": "not found"}, request=request)
            patch = json.loads(request.content or b"{}")
            # `created` is onCreate-only; a client cannot move it.
            patch.pop("created", None)
            found.update(patch)
            found["updated"] = _now()
            return httpx.Response(200, json=found, request=request)
        if request.method == "DELETE":
            found = self._find(rows, record_id)
            if found is None:
                return httpx.Response(404, json={"message": "not found"}, request=request)
            rows.remove(found)
            return httpx.Response(204, request=request)
        return httpx.Response(405, json={"message": "method not allowed"}, request=request)

    def _create(self, request, collection, rows):
        body = json.loads(request.content or b"{}")
        if collection == "course_materials" and self._violates_source_index(rows, body):
            return httpx.Response(
                400,
                json={"message": "Failed to create record.",
                      "data": {"source_identity": {"code": "validation_not_unique"}}},
                request=request,
            )
        # `created`/`updated` are server-managed autodate fields; any
        # client-supplied value for either is ignored, same as real PocketBase.
        stamp = _now()
        record = {"id": body.get("id") or new_id(), **body, "created": stamp, "updated": stamp}
        rows.append(record)
        return httpx.Response(200, json=record, request=request)

    @staticmethod
    def _violates_source_index(rows, body) -> bool:
        """The index is partial: rows with no Source Identity are outside it."""
        identity = body.get("source_identity", "")
        if not identity:
            return False
        return any(
            row.get("student") == body.get("student") and row.get("source_identity") == identity
            for row in rows
        )

    @staticmethod
    def _find(rows, record_id):
        return next((row for row in rows if row.get("id") == record_id), None)

    def _list(self, request, rows):
        query = parse_qs(request.url.query.decode())
        expression = query.get("filter", [""])[0]
        matched = [row for row in rows if self._matches(row, expression)]

        sort = query.get("sort", [""])[0]
        if sort:
            field = sort.lstrip("-+")
            matched.sort(key=lambda row: row.get(field) or "", reverse=sort.startswith("-"))

        per_page = int(query.get("perPage", ["200"])[0])
        page = matched[:per_page]
        return httpx.Response(
            200,
            json={"page": 1, "perPage": per_page, "totalItems": len(matched), "items": page},
            request=request,
        )

    @staticmethod
    def _matches(row, expression: str) -> bool:
        if not expression:
            return True
        clauses = _CLAUSE.findall(expression)
        if not clauses:
            raise AssertionError(
                f"The fake only understands equality conjunctions; got {expression!r}. "
                "If the repository now emits something else, teach both the fake and "
                "tests/test_pocketbase_contract.py about it."
            )
        return all(str(row.get(field, "")) == value for field, value in clauses)
