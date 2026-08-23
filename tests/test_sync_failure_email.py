from app.suppliers import scheduler


def test_sync_failure_email_contains_actionable_job_details(monkeypatch):
    sent = {}

    class SMTP:
        def __init__(self, host, port, **kwargs):
            sent["connection"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def login(self, username, password):
            sent["login"] = (username, password)

        def send_message(self, message):
            sent["message"] = message

    monkeypatch.setattr(
        scheduler, "_smtp_credentials", lambda: ("mail@weldingshop.nl", "secret")
    )
    monkeypatch.setattr(scheduler.smtplib, "SMTP_SSL", SMTP)
    scheduler._send_sync_failure_email(
        supplier={"slug": "valkenpower", "name": "Valkenpower"},
        job_id="job-123",
        error=RuntimeError("Shopify tijdelijk niet beschikbaar"),
        progress=31,
        started_at="2026-08-20T01:00:00+00:00",
        finished_at="2026-08-20T01:01:00+00:00",
        scheduled=True,
    )

    message = sent["message"]
    assert sent["connection"] == ("www49.totaalholding.nl", 465)
    assert sent["login"] == ("mail@weldingshop.nl", "secret")
    assert message["To"] == "mail@weldingshop.nl"
    assert message["Subject"] == "PIM synchronisatie fout"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "Valkenpower" in body
    assert "job-123" in body
    assert "31%" in body
    assert "Shopify tijdelijk niet beschikbaar" in body
    assert "Willem Bangma" in body
    assert "Uw PIM manager" in body


def test_smtp_credentials_are_read_from_authinfo(monkeypatch, tmp_path):
    authinfo = tmp_path / "authinfo"
    authinfo.write_text(
        'AuthInfo:www49.totaalholding.nl "U:root" '
        '"I:mail@weldingshop.nl" "P:password" "M:PLAIN"\n'
    )
    monkeypatch.setattr(scheduler, "SMTP_AUTHINFO_PATH", authinfo)

    assert scheduler._smtp_credentials() == ("mail@weldingshop.nl", "password")
