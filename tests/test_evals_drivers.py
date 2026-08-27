"""
The drivers, against stubs.

What is tested here is the driver's own logic -- what it sends, what it reads
back, and when it gives up. Whether Gemini confabulates is not a thing a unit
test can or should decide; that is what the eval run is for.
"""
import httpx
import pytest

from evals.drivers.base import DriverError, Student
from evals.drivers.chat_http import ChatHttpDriver

STUDENT = Student(student_id="aaaaaaaaaa11111", token="a-token")


def driver_over(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")
    return ChatHttpDriver(client, "http://testserver"), client


async def test_the_driver_returns_what_the_tutor_said():
    def handler(request):
        return httpx.Response(200, json={"response": "I have no memory of you.", "history": []})

    driver, client = driver_over(handler)
    async with client:
        assert await driver.ask(STUDENT, "What do you remember?") == "I have no memory of you."


async def test_the_driver_sends_the_prompt_as_the_message():
    import json

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok", "history": []})

    driver, client = driver_over(handler)
    async with client:
        await driver.ask(STUDENT, "What do you remember about me?")

    assert seen["body"]["message"] == "What do you remember about me?"


async def test_the_driver_sends_no_history():
    """Every run is a first turn. Carrying history would let run N see run N-1."""
    import json

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"response": "ok", "history": []})

    driver, client = driver_over(handler)
    async with client:
        await driver.ask(STUDENT, "hi")

    assert seen["body"]["history"] == []


async def test_the_driver_authenticates_as_the_student():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"response": "ok", "history": []})

    driver, client = driver_over(handler)
    async with client:
        await driver.ask(STUDENT, "hi")

    assert seen["auth"] == "Bearer a-token"


async def test_an_error_status_voids_rather_than_returning_text():
    """A 500 body is not something to judge."""

    def handler(request):
        return httpx.Response(500, json={"detail": "Failed to generate a response"})

    driver, client = driver_over(handler)
    async with client:
        with pytest.raises(DriverError):
            await driver.ask(STUDENT, "hi")


async def test_an_empty_answer_voids():
    """The route substitutes an apology when Gemini returns nothing; judging that
    would record a CLEAN run the tutor never actually produced."""

    def handler(request):
        return httpx.Response(200, json={"response": "", "history": []})

    driver, client = driver_over(handler)
    async with client:
        with pytest.raises(DriverError):
            await driver.ask(STUDENT, "hi")


async def test_a_transport_failure_voids():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    driver, client = driver_over(handler)
    async with client:
        with pytest.raises(DriverError):
            await driver.ask(STUDENT, "hi")


async def test_the_driver_names_its_surface():
    """The report groups by this, and cases select on it."""
    driver, client = driver_over(lambda r: httpx.Response(200, json={"response": "x", "history": []}))
    async with client:
        assert driver.name == "chat"
