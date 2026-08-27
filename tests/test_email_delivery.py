"""Getting email out, and saying so honestly when it does not.

Every failure here used to look identical from the outside: the invite endpoint
returns 201 whether the message was delivered, logged, queued into a list no
worker reads, or rejected by the provider. These pin down the difference.
"""

import pytest

from app.services import email_service


@pytest.fixture
def no_worker(monkeypatch):
    """Broker reachable, nobody consuming — the silent-drop case."""
    monkeypatch.setattr(email_service, "_worker_probe", None)
    monkeypatch.setattr(email_service, "_celery_worker_listening", lambda: False)


@pytest.fixture
def with_worker(monkeypatch):
    monkeypatch.setattr(email_service, "_worker_probe", None)
    monkeypatch.setattr(email_service, "_celery_worker_listening", lambda: True)


def test_delivery_falls_back_to_inline_when_no_worker_is_listening(
    monkeypatch, no_worker
):
    sent: list[str] = []
    monkeypatch.setattr(
        email_service, "send_email", lambda to, subject, html, name=None: sent.append(to)
    )

    outcome = email_service.deliver_email("teacher@test.com", "Hi", "<p>Hi</p>")

    assert outcome == "sent"
    assert sent == ["teacher@test.com"]


def test_delivery_reports_failure_instead_of_raising(monkeypatch, no_worker):
    """The account row is already committed; a raise here would misreport it."""

    def explode(*args, **kwargs):
        raise email_service.EmailDeliveryError("Mailjet rejected the API key")

    monkeypatch.setattr(email_service, "send_email", explode)

    assert email_service.deliver_email("teacher@test.com", "Hi", "<p>Hi</p>") == "failed"


def test_delivery_falls_back_when_handing_to_celery_raises(monkeypatch, with_worker):
    """A worker answered the ping but the broker died before the handoff."""
    sent: list[str] = []
    monkeypatch.setattr(
        email_service, "send_email", lambda to, subject, html, name=None: sent.append(to)
    )

    class Broken:
        def delay(self, *args, **kwargs):
            raise ConnectionError("redis is gone")

    monkeypatch.setattr("app.tasks.email_tasks.send_email_task", Broken())

    assert email_service.deliver_email("teacher@test.com", "Hi", "<p>Hi</p>") == "sent"
    assert sent == ["teacher@test.com"]


def test_worker_probe_is_cached(monkeypatch):
    """One ping a minute: this runs on the path of every invite sent."""
    monkeypatch.setattr(email_service, "_worker_probe", None)
    calls = {"n": 0}

    class FakeControl:
        def ping(self, timeout=None):
            calls["n"] += 1
            return [{"worker@host": {"ok": "pong"}}]

    class FakeApp:
        control = FakeControl()

    monkeypatch.setitem(
        __import__("sys").modules, "celery_worker", type("M", (), {"celery_app": FakeApp()})
    )

    assert email_service._celery_worker_listening() is True
    assert email_service._celery_worker_listening() is True
    assert calls["n"] == 1


async def test_email_test_endpoint_reports_an_unconfigured_provider(
    client, admin_token, monkeypatch
):
    monkeypatch.setattr(
        type(email_service.settings), "email_backend", property(lambda self: "log")
    )

    response = await client.post(
        "/admin/system/email-test",
        headers={"Authorization": admin_token},
        json={"to_email": "me@example.com"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivered"] is False
    assert body["backend"] == "log"
    assert "No email provider" in body["error"]


async def test_email_test_endpoint_surfaces_the_providers_own_error(
    client, admin_token, monkeypatch
):
    """The whole point: the provider's words, not a generic failure."""
    monkeypatch.setattr(
        type(email_service.settings), "email_backend", property(lambda self: "mailjet")
    )

    def explode(*args, **kwargs):
        raise email_service.EmailDeliveryError(
            "Mailjet could not send to me@example.com - . 'From' email is not "
            "an authorised sender"
        )

    monkeypatch.setattr(email_service, "send_email", explode)

    response = await client.post(
        "/admin/system/email-test",
        headers={"Authorization": admin_token},
        json={"to_email": "me@example.com"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivered"] is False
    assert "authorised sender" in body["error"]


async def test_email_test_endpoint_calls_out_sandbox_mode(
    client, admin_token, monkeypatch
):
    """A clean 200 from Mailjet still delivers nothing in sandbox mode."""
    monkeypatch.setattr(
        type(email_service.settings), "email_backend", property(lambda self: "mailjet")
    )
    monkeypatch.setattr(email_service.settings, "MAILJET_SANDBOX", True)
    monkeypatch.setattr(email_service, "send_email", lambda *a, **k: None)

    response = await client.post(
        "/admin/system/email-test",
        headers={"Authorization": admin_token},
        json={"to_email": "me@example.com"},
    )
    body = response.json()
    assert body["delivered"] is False
    assert "SANDBOX" in body["error"]


async def test_email_test_endpoint_is_super_admin_only(client, school_admin_token):
    response = await client.post(
        "/admin/system/email-test",
        headers={"Authorization": school_admin_token},
        json={"to_email": "me@example.com"},
    )
    assert response.status_code == 403
