"""Appointment ⇄ transcript cross-reference (Part A) + native Slack approve (Part B).
Pure functions and the signature-verified endpoint — no network."""
import hmac, hashlib, json, time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import jwt
import pytest
from fastapi.testclient import TestClient


def _appt(**over):
    base = {"leadId": "123", "stage": "pending", "company": "Great Smile Dental",
            "date": "2026-10-23T10:00", "area": "Las Vegas NV", "createdBy": "cristine",
            "createdAt": datetime.utcnow().isoformat() + "Z", "history": []}
    base.update(over)
    return base


# ── Part A ───────────────────────────────────────────────────────────────────

class TestNormalizeChecks:
    def test_every_enum_code_maps_to_its_fixed_severity(self, m):
        raw = [{"code": c, "detail": "d", "evidence": "they said so"} for c in m.APPT_CHECK_SEVERITY]
        out = m.normalize_appt_checks(raw)
        got = {c["code"]: c["severity"] for c in out}
        for code, sev in m.APPT_CHECK_SEVERITY.items():
            if code == "all_clear":
                continue           # all_clear only appears when nothing else did
            assert got[code] == sev
        # ordered block → warn
        ranks = [m._APPT_SEV_RANK[c["severity"]] for c in out]
        assert ranks == sorted(ranks)

    def test_model_cannot_choose_severity(self, m):
        out = m.normalize_appt_checks([{"code": "signer_absent", "severity": "block",
                                        "detail": "d", "evidence": "someone else signs"}])
        assert out[0]["severity"] == "warn"

    def test_unknown_code_dropped_never_crashes(self, m, capsys):
        out = m.normalize_appt_checks([{"code": "vibes_off", "detail": "d", "evidence": "q"},
                                       "garbage", None, 42])
        assert [c["code"] for c in out] == ["all_clear"]
        assert "unknown check code" in capsys.readouterr().out

    def test_no_quote_no_check(self, m):
        out = m.normalize_appt_checks([{"code": "company_mismatch", "detail": "d", "evidence": "   "}])
        assert [c["code"] for c in out] == ["all_clear"]

    def test_evidence_clipped_to_25_words_and_deduped(self, m):
        long = " ".join(f"w{i}" for i in range(40))
        out = m.normalize_appt_checks([
            {"code": "company_mismatch", "detail": "d", "evidence": long},
            {"code": "company_mismatch", "detail": "again", "evidence": "second"}])
        assert len(out) == 1
        assert out[0]["evidence"].endswith("…")
        assert len(out[0]["evidence"].rstrip("…").split()) == 25

    def test_empty_reads_as_all_clear(self, m):
        assert m.normalize_appt_checks([])[0]["code"] == "all_clear"
        assert m.normalize_appt_checks(None)[0]["severity"] == "ok"
        assert m.appt_checks_worst(m.normalize_appt_checks([])) == "ok"

    def test_worst_severity(self, m):
        assert m.appt_checks_worst([{"severity": "warn"}, {"severity": "block"}]) == "block"
        assert m.appt_checks_has_block([{"severity": "warn"}]) is False


class TestLinkTranscript:
    @pytest.fixture
    def spy(self, m, monkeypatch, no_network):
        calls = {"set": [], "notify": [], "verify": 0}
        monkeypatch.setattr(m, "_settings_set_json", lambda k, v: calls["set"].append((k, v)) or True)
        monkeypatch.setattr(m, "_notify_appt_transcript", lambda a, c: calls["notify"].append((a, c)))
        def verify(appt, text, call_at=""):
            calls["verify"] += 1
            return m.normalize_appt_checks([{"code": "signer_absent", "detail": "d", "evidence": "the owner signs"}])
        monkeypatch.setattr(m, "_verify_appointment_transcript", verify)
        return calls

    def test_second_analysis_in_window_does_not_overwrite_or_post(self, m, monkeypatch, spy):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt(transcript_id=77))
        out = m._link_transcript_to_appointment(901, "123", {"summary": "later call"}, 78, full_text="x" * 300)
        assert out is None
        assert spy["set"] == [] and spy["notify"] == [] and spy["verify"] == 0

    def test_appt_created_25h_ago_is_not_the_booking_call(self, m, monkeypatch, spy):
        old = (datetime.utcnow() - timedelta(hours=25)).isoformat() + "Z"
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt(createdAt=old))
        out = m._link_transcript_to_appointment(901, "123", {"summary": "s"}, 78, full_text="x" * 300)
        assert out is None
        assert spy["set"] == [] and spy["notify"] == []

    def test_no_appointment_no_work(self, m, monkeypatch, spy):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: None)
        assert m._link_transcript_to_appointment(901, "123", {"summary": "s"}, 78, full_text="t") is None
        assert spy["verify"] == 0

    def test_first_transcript_links_persists_and_posts(self, m, monkeypatch, spy):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt())
        checks = m._link_transcript_to_appointment(None, "123", {"summary": "S" * 700, "next_step": "walkthrough Thu"},
                                                   78, full_text="x" * 300, now=datetime(2026, 10, 20, 17, 0))
        assert [c["code"] for c in checks] == ["signer_absent"]
        (key, saved), = spy["set"]
        assert key == "appt_123"
        assert saved["transcript_id"] == 78
        assert saved["transcript_call_id"] is None
        assert len(saved["transcript_summary"]) == 600
        assert saved["transcript_next_step"] == "walkthrough Thu"
        assert saved["transcript_checks"] == checks
        assert saved["transcript_linked_at"] == "2026-10-20T17:00:00Z"
        # surfaced, never applied: the record's own fields are untouched
        assert saved["company"] == "Great Smile Dental" and saved["date"] == "2026-10-23T10:00"
        assert len(spy["notify"]) == 1

    def test_persist_failure_still_posts(self, m, monkeypatch, spy):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt())
        monkeypatch.setattr(m, "_settings_set_json", lambda k, v: False)
        m._link_transcript_to_appointment(None, "123", {"summary": "s"}, 78, full_text="x")
        assert len(spy["notify"]) == 1


class TestVerifyFallback:
    def test_analysis_disabled_links_as_not_verified(self, m, monkeypatch, no_network):
        monkeypatch.setattr(m, "ANALYZE_ENABLED", False)
        out = m._verify_appointment_transcript(_appt(), "x" * 300)
        assert out == [{"code": "not_verified", "severity": "warn",
                        "detail": "analysis unavailable (ANALYZE_ENABLED=0 or no API key)", "evidence": ""}]

    def test_model_error_is_not_verified(self, m, monkeypatch, no_network):
        monkeypatch.setattr(m, "ANALYZE_ENABLED", True)
        monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "k")
        import anthropic
        class BadClient:
            def __init__(self, **k): pass
            class messages:
                @staticmethod
                def create(**k): raise RuntimeError("api down")
        monkeypatch.setattr(anthropic, "Anthropic", BadClient)
        out = m._verify_appointment_transcript(_appt(), "x" * 300)
        assert out[0]["code"] == "not_verified" and out[0]["severity"] == "warn"


class TestTranscriptSlackMessage:
    @pytest.fixture
    def posted(self, m, monkeypatch):
        box = []
        class R:
            status_code = 200; text = ""
        class Lib:
            def post(self, url, json=None, timeout=None):
                box.append(json); return R()
        monkeypatch.setattr(m, "req_lib", Lib())
        return box

    @staticmethod
    def _action_ids(msg):
        acts = next(b for b in msg["blocks"] if b["type"] == "actions")
        return [e.get("action_id") for e in acts["elements"]]

    def test_block_check_removes_approve_button(self, m, monkeypatch, posted):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "s")
        checks = m.normalize_appt_checks([{"code": "company_mismatch", "detail": "Record says Great Smile Dental",
                                           "evidence": "we're Absolute Demo, not the dental office"}])
        m._notify_appt_transcript(_appt(transcript_summary="They are Absolute Demo."), checks)
        msg, = posted
        assert msg["blocks"][0]["text"]["text"].startswith("🛑 Transcript check — Great Smile Dental: needs a look")
        ids = self._action_ids(msg)
        assert "appt_approve" not in ids and "appt_reject" not in ids
        assert ids == ["appt_transcript", "appt_open"]
        checks_txt = msg["blocks"][2]["text"]["text"]
        assert "🛑 `company_mismatch`" in checks_txt and "Absolute Demo" in checks_txt

    def test_warn_keeps_native_approve(self, m, monkeypatch, posted):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "s")
        checks = m.normalize_appt_checks([{"code": "signer_absent", "detail": "d", "evidence": "my boss signs"}])
        m._notify_appt_transcript(_appt(), checks)
        msg, = posted
        assert msg["blocks"][0]["text"]["text"].startswith("⚠️ Transcript check — ")
        ids = self._action_ids(msg)
        assert ids[0] == "appt_approve" and "appt_reject" not in ids
        approve = next(b for b in msg["blocks"] if b["type"] == "actions")["elements"][0]
        tok = jwt.decode(approve["value"], m.SECRET_KEY, algorithms=[m.ALGORITHM])
        assert tok == {"leadId": "123", "act": "approve_appt", "exp": tok["exp"]}

    def test_all_clear_header_and_url_fallback_button(self, m, monkeypatch, posted):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "")
        m._notify_appt_transcript(_appt(), m.normalize_appt_checks([]))
        msg, = posted
        assert msg["blocks"][0]["text"]["text"] == "🎙️ Transcript in — Great Smile Dental"
        assert "✅ Transcript agrees with the booking." in msg["blocks"][2]["text"]["text"]
        first = next(b for b in msg["blocks"] if b["type"] == "actions")["elements"][0]
        assert "action_id" not in first and "/appt-approve?t=" in first["url"]

    def test_already_approved_appointment_gets_no_approve_button(self, m, monkeypatch, posted):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "s")
        m._notify_appt_transcript(_appt(stage="approved"), m.normalize_appt_checks([]))
        assert "appt_approve" not in self._action_ids(posted[0])


class TestNoTranscriptLine:
    def test_reasons(self, m):
        a = _appt()
        assert "under the" in m.appt_no_transcript_line(a, {"reason": "short", "duration": 31})
        assert "31s" in m.appt_no_transcript_line(a, {"reason": "short", "duration": 31})
        assert "answering machine" in m.appt_no_transcript_line(a, {"reason": "machine"})
        assert "logged as voicemail" in m.appt_no_transcript_line(a, {"reason": "disposition"}, "voicemail")
        generic = m.appt_no_transcript_line(a, {})
        assert generic.startswith("🔇 No transcript for the Great Smile Dental appointment")
        assert f"under {m.TRANSCRIBE_MIN_SEC}s" in generic and "outside the dialer" in generic

    def test_timeout_job_never_raises_and_does_nothing_when_linked(self, m, monkeypatch, no_network):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt(transcript_id=5))
        m.appt_transcript_timeout({"lead_id": "123"})          # no network call → no AssertionError
        monkeypatch.setattr(m, "_settings_get_json", lambda k: (_ for _ in ()).throw(RuntimeError("db")))
        m.appt_transcript_timeout({"lead_id": "123"})          # swallowed, logged

    def test_handler_registered(self, m):
        assert m._JOB_HANDLERS["appt_transcript_timeout"] is m.appt_transcript_timeout


class TestAngeloContext:
    def test_angelo_card_carries_booking_call_context(self, m, monkeypatch):
        box = []
        class R: status_code = 200
        class Lib:
            def post(self, url, json=None, timeout=None): box.append(json); return R()
        monkeypatch.setattr(m, "req_lib", Lib())
        checks = [{"code": "signer_absent", "severity": "warn", "detail": "Boss signs", "evidence": "my boss signs"}]
        m._notify_angelo(_appt(stage="approved", transcript_summary="Agreed to Thu 10am.",
                               transcript_next_step="Walkthrough Thursday", transcript_checks=checks))
        txt = box[0]["blocks"][1]["text"]["text"]
        assert "*From the booking call:* Agreed to Thu 10am." in txt
        assert "*Prospect's next step:* Walkthrough Thursday" in txt
        assert "⚠️ `signer_absent`" in txt

    def test_angelo_card_unchanged_without_transcript(self, m, monkeypatch):
        box = []
        class R: status_code = 200
        class Lib:
            def post(self, url, json=None, timeout=None): box.append(json); return R()
        monkeypatch.setattr(m, "req_lib", Lib())
        m._notify_angelo(_appt(stage="approved"))
        assert "booking call" not in box[0]["blocks"][1]["text"]["text"]


# ── Part B ───────────────────────────────────────────────────────────────────

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"

def _sign(body: bytes, ts: int, secret=SECRET):
    return "v0=" + hmac.new(secret.encode(), b"v0:%d:" % ts + body, hashlib.sha256).hexdigest()


class TestSignature:
    def test_valid_passes(self, m):
        body = b"payload=%7B%7D"; ts = int(time.time())
        assert m._slack_sig_ok(SECRET, str(ts), _sign(body, ts), body) is True

    def test_stale_timestamp_fails(self, m):
        body = b"x"; ts = int(time.time()) - 301
        assert m._slack_sig_ok(SECRET, str(ts), _sign(body, ts), body) is False

    def test_tampered_body_fails(self, m):
        ts = int(time.time())
        assert m._slack_sig_ok(SECRET, str(ts), _sign(b"payload=a", ts), b"payload=b") is False

    def test_missing_secret_or_headers_fail(self, m):
        ts = int(time.time())
        assert m._slack_sig_ok("", str(ts), _sign(b"x", ts), b"x") is False
        assert m._slack_sig_ok(SECRET, "", _sign(b"x", ts), b"x") is False
        assert m._slack_sig_ok(SECRET, str(ts), "", b"x") is False
        assert m._slack_sig_ok(SECRET, "not-a-number", _sign(b"x", ts), b"x") is False


class TestEndpoint:
    @pytest.fixture
    def client(self, m):
        return TestClient(m.app)

    def _post(self, client, body: bytes, ts=None, sig=None, secret=SECRET):
        ts = int(time.time()) if ts is None else ts
        return client.post("/slack/interactions", content=body,
                           headers={"Content-Type": "application/x-www-form-urlencoded",
                                    "X-Slack-Request-Timestamp": str(ts),
                                    "X-Slack-Signature": sig if sig is not None else _sign(body, ts, secret)})

    def test_no_secret_is_503_never_open(self, m, monkeypatch, client):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "")
        assert self._post(client, b"payload=%7B%7D").status_code == 503

    def test_bad_signature_401(self, m, monkeypatch, client):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", SECRET)
        assert self._post(client, b"payload=%7B%7D", sig="v0=dead").status_code == 401
        assert self._post(client, b"payload=%7B%7D", ts=int(time.time()) - 600).status_code == 401

    def test_valid_block_actions_acks_and_dispatches_on_thread(self, m, monkeypatch, client):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", SECRET)
        spawned = []
        class FakeThread:
            def __init__(self, target=None, args=(), **k): spawned.append((target, args))
            def start(self): pass
        monkeypatch.setattr(m.threading, "Thread", FakeThread)
        payload = {"type": "block_actions", "user": {"id": "U1"}, "actions": [{"action_id": "appt_ping"}]}
        r = self._post(client, urlencode({"payload": json.dumps(payload)}).encode())
        assert r.status_code == 200 and r.content == b""
        (target, args), = spawned
        assert target is m._dispatch_slack_interaction and args[0]["type"] == "block_actions"

    def test_non_block_actions_acked_without_dispatch(self, m, monkeypatch, client):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", SECRET)
        spawned = []
        class FakeThread:
            def __init__(self, **k): spawned.append(1)
            def start(self): pass
        monkeypatch.setattr(m.threading, "Thread", FakeThread)
        r = self._post(client, urlencode({"payload": json.dumps({"type": "view_submission"})}).encode())
        assert r.status_code == 200 and spawned == []


class TestActions:
    @pytest.fixture
    def env(self, m, monkeypatch, no_network):
        monkeypatch.setattr(m, "SLACK_APPROVER_USER_IDS", {"U_ERIC"})
        calls = {"approve": [], "set": []}
        def approve(lead_id, by):
            calls["approve"].append((lead_id, by))
            return _appt(stage="approved")
        monkeypatch.setattr(m, "_approve_appointment", approve)
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt())
        monkeypatch.setattr(m, "_settings_set_json", lambda k, v: calls["set"].append((k, v)) or True)
        return calls

    def _payload(self, m, action_id, uid="U_ERIC", value=None, uname="eric"):
        return {"type": "block_actions", "user": {"id": uid, "username": uname},
                "response_url": "http://127.0.0.1:9/r",
                "actions": [{"action_id": action_id, "value": value if value is not None else m._appt_approve_token("123")}]}

    def test_non_listed_user_gets_ephemeral_deny_and_nothing_happens(self, m, env):
        msg = m._slack_action_result(self._payload(m, "appt_approve", uid="U_RANDO", uname="rando"))
        assert msg["response_type"] == "ephemeral" and msg["replace_original"] is False
        assert "not on the approver list" in msg["text"]
        assert env["approve"] == [] and env["set"] == []

    def test_expired_jwt_says_approve_from_board_not_500(self, m, env):
        tok = jwt.encode({"leadId": "123", "act": "approve_appt",
                          "exp": datetime.utcnow() - timedelta(seconds=5)}, m.SECRET_KEY, algorithm=m.ALGORITHM)
        msg = m._slack_action_result(self._payload(m, "appt_approve", value=tok))
        assert msg["replace_original"] is True and "Link expired" in msg["text"] and "board" in msg["text"]
        assert env["approve"] == []

    def test_wrong_act_token_denied(self, m, env):
        tok = jwt.encode({"sub": "cristine", "role": "caller"}, m.SECRET_KEY, algorithm=m.ALGORITHM)
        msg = m._slack_action_result(self._payload(m, "appt_approve", value=tok))
        assert msg["response_type"] == "ephemeral" and env["approve"] == []

    def test_approve_goes_through_shared_function(self, m, env):
        msg = m._slack_action_result(self._payload(m, "appt_approve"), now=datetime(2026, 10, 20, 15, 42))
        assert env["approve"] == [("123", "slack:eric")]
        assert msg["replace_original"] is True
        assert msg["text"].startswith("✅ Approved by @eric · 11:42 AM ET — sent to Angelo")
        assert "Great Smile Dental" in msg["blocks"][0]["text"]["text"]

    def test_double_tap_replaces_same_message_without_second_angelo(self, m, env, monkeypatch):
        monkeypatch.setattr(m, "_settings_get_json", lambda k: _appt(stage="approved"))
        msg = m._slack_action_result(self._payload(m, "appt_approve"))
        # _approve_appointment itself owns the no-double-ping (`already`); here we
        # only assert the message says so and the function was still routed once.
        assert "Already approved" in msg["text"] and len(env["approve"]) == 1

    def test_reject_sets_lost_with_history_reason(self, m, env):
        msg = m._slack_action_result(self._payload(m, "appt_reject"), now=datetime(2026, 10, 20, 15, 42))
        (key, saved), = env["set"]
        assert key == "appt_123" and saved["stage"] == "lost"
        assert saved["history"][-1] == {"stage": "lost", "at": "2026-10-20T15:42:00Z", "by": "slack:eric",
                                        "reason": "rejected_from_slack"}
        assert "lost" in m.APPT_STAGES
        assert msg["text"].startswith("❌ Rejected by @eric · 11:42 AM ET")
        assert env["approve"] == []

    def test_ping_rewrites_itself(self, m, env):
        msg = m._slack_action_result(self._payload(m, "appt_ping", uid="U_ANYONE", uname="sam", value="ping"))
        assert msg["replace_original"] is True and "tapped by @sam" in msg["text"]

    def test_url_button_and_unknown_actions_are_silent(self, m, env):
        assert m._slack_action_result(self._payload(m, "appt_open")) == {}
        assert m._slack_action_result({"type": "block_actions", "user": {}, "actions": []}) == {}


class TestPendingPingButtons:
    def _elements(self, m, monkeypatch):
        box = []
        class R: status_code = 200
        class Lib:
            def post(self, url, json=None, timeout=None): box.append(json); return R()
        monkeypatch.setattr(m, "req_lib", Lib())
        m._notify_pending_appointment(_appt())
        return next(b for b in box[0]["blocks"] if b["type"] == "actions")["elements"]

    def test_native_mode(self, m, monkeypatch):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "s")
        els = self._elements(m, monkeypatch)
        assert [e.get("action_id") for e in els] == ["appt_approve", "appt_reject", "appt_open"]
        assert els[0]["style"] == "primary" and els[1]["style"] == "danger"
        assert els[0]["confirm"]["text"]["text"].startswith("Great Smile Dental · 2026-10-23")
        assert jwt.decode(els[0]["value"], m.SECRET_KEY, algorithms=[m.ALGORITHM])["leadId"] == "123"
        assert els[2]["url"].startswith("http")

    def test_url_fallback_mode(self, m, monkeypatch):
        monkeypatch.setattr(m, "SLACK_SIGNING_SECRET", "")
        els = self._elements(m, monkeypatch)
        assert "action_id" not in els[0] and "/appt-approve?t=" in els[0]["url"]
        assert len(els) == 2
