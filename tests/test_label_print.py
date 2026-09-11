import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.web import label_print

PNG = b"\x89PNG\r\n\x1a\nexample"


def test_pairing_token_and_installer(tmp_path):
    queue = label_print.PrintQueue(tmp_path / "print.sqlite")
    with zipfile.ZipFile(io.BytesIO(queue.installer())) as archive:
        config = json.loads(archive.read("config.json"))
        assert config["printer"] == label_print.PRINTER
        assert config["url"].startswith("https://")
        assert "Installeren.cmd" in archive.namelist()
    assert queue.authorized(config["token"])
    assert not queue.authorized("")
    assert not queue.authorized("wrong-token")
    assert not queue.status()["online"]


def test_claim_once_and_idempotent_submission(tmp_path):
    queue = label_print.PrintQueue(tmp_path / "print.sqlite")
    identifier = queue.enqueue(PNG, 2, "Label", "one-click")
    assert queue.enqueue(PNG, 2, "Label", "one-click") == identifier
    with ThreadPoolExecutor(2) as executor:
        claims = list(executor.map(lambda _: queue.claim(label_print.PRINTER), range(2)))
    jobs = [job for job in claims if job]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["copies"] == 2
    assert queue.status()["online"]
    with pytest.raises(ValueError):
        queue.complete(identifier, "wrong-claim", "submitted")
    queue.complete(identifier, job["claim"], "submitted")
    queue.complete(identifier, job["claim"], "submitted")
    assert queue.job(identifier)["state"] == "submitted"
    assert queue.claim(label_print.PRINTER) is None


def test_offline_expired_and_uncertain_jobs_are_not_printed_again(tmp_path):
    queue = label_print.PrintQueue(tmp_path / "print.sqlite")
    identifier = queue.enqueue(PNG, 1, "Label", "first")
    assert queue.claim("another-printer") is None
    assert queue.claim(label_print.PRINTER, "Printer ontbreekt") is None
    assert not queue.status()["online"]
    job = queue.claim(label_print.PRINTER)
    with queue.connect() as db:
        db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time() - 700, identifier))
    assert queue.claim(label_print.PRINTER) is None
    assert queue.job(identifier)["state"] == "uncertain"
    queue.complete(identifier, job["claim"], "submitted")
    assert queue.job(identifier)["state"] == "submitted"
    expired = queue.enqueue(PNG, 1, "Label", "second")
    with queue.connect() as db:
        db.execute("UPDATE jobs SET created=? WHERE id=?", (time.time() - 700, expired))
    assert queue.claim(label_print.PRINTER) is None
    assert queue.job(expired)["state"] == "expired"


def test_api_authentication_and_payload_validation(monkeypatch, tmp_path):
    queue = label_print.PrintQueue(tmp_path / "print.sqlite")
    monkeypatch.setattr(label_print, "PrintQueue", lambda: queue)
    class Handler:
        path = "/label-print/claim"
        headers = {}
        rfile = io.BytesIO(b'{}')
        def _reply(self, status, payload):
            self.status, self.payload = status, payload
    handler = Handler()
    label_print.handle_print_request(handler)
    assert handler.status == 401
    with queue.connect() as db:
        token = db.execute("SELECT token FROM bridge").fetchone()[0]
    handler.headers = {"Authorization": "Bearer " + token, "Content-Length": "5000"}
    label_print.handle_print_request(handler)
    assert handler.status == 400
    body = json.dumps({"printer": label_print.PRINTER}).encode()
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = io.BytesIO(body)
    label_print.handle_print_request(handler)
    assert handler.status == 200 and handler.payload == {"job": None}
