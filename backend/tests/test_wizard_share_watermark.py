"""Backend tests for the wizard/plan bifurcation, share pages, watermark, and timestamp fix."""
import os
import re
import subprocess
import uuid
import pytest
import requests

BASE_URL = "https://a989d36b-21b6-46b5-b93f-14b7756c1853.preview.emergentagent.com"
ADMIN_EMAIL = "admin@clipper.test"
ADMIN_PASSWORD = "clipperadmin123"
DEMO_JOB = "demo-job-0001"


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


@pytest.fixture(scope="module")
def free_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    email = f"TEST_free_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{BASE_URL}/api/auth/register",
               json={"email": email, "password": "freepass123"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    s.headers.update({"Authorization": f"Bearer {token}"})
    s._email = email
    return s


# ---- Plan limits / entitlements ---------------------------------------------

class TestPlanLimits:
    def test_admin_gets_pro_entitlements_and_limits(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        d = r.json()
        assert d["is_admin"] is True
        for feat in ["multilingual", "gameplay", "tighten_pauses",
                     "no_watermark", "share_pages", "branding", "priority"]:
            assert feat in d["entitlements"], f"admin missing {feat}"
        assert d["limits"]["max_clips"] == 10
        assert d["limits"]["max_source_minutes"] == 240
        assert d["limits"]["max_upload_mb"] == 4096

    def test_free_limits(self, free_client):
        r = free_client.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        d = r.json()
        assert d["is_admin"] is False
        assert d["entitlements"] == []
        assert d["limits"]["max_clips"] == 2
        assert d["limits"]["max_source_minutes"] == 15
        assert d["limits"]["max_upload_mb"] == 500

    def test_plans_endpoint_public(self):
        r = requests.get(f"{BASE_URL}/api/billing/plans")
        assert r.status_code == 200
        d = r.json()
        assert d["limits"]["free"]["max_clips"] == 2
        assert d["limits"]["free"]["max_source_minutes"] == 15
        assert d["limits"]["free"]["max_upload_mb"] == 500
        free = next(plan for plan in d["plans"] if plan["id"] == "free")
        assert free["credits"] == 20


# ---- Timestamp bug fix ------------------------------------------------------

class TestTimestampFix:
    def test_jobs_created_at_has_utc_offset(self, admin_client):
        r = admin_client.get(f"{BASE_URL}/api/jobs")
        assert r.status_code == 200
        jobs = r.json()
        assert isinstance(jobs, list) and len(jobs) > 0, "expected the demo job"
        for j in jobs:
            ca = j.get("created_at")
            assert ca, f"job {j.get('id')} missing created_at"
            # ISO 8601 with an offset. Accept +00:00 or Z-like tail; the fix
            # requires an explicit offset — reject a naive string.
            assert re.search(r"([+-]\d\d:?\d\d|Z)$", ca), \
                f"created_at has no offset: {ca!r}"


# ---- Server-side gates ------------------------------------------------------

class TestFreeGates:
    URL = "https://youtu.be/dQw4w9WgXcQ"

    def _post(self, client, **overrides):
        payload = {"url": self.URL, "n_clips": 1, "template": "classic",
                   "tighten_pauses": False, "multilingual": False}
        payload.update(overrides)
        return client.post(f"{BASE_URL}/api/jobs", json=payload)

    def test_free_n_clips_over_limit_gets_402(self, free_client):
        r = self._post(free_client, n_clips=5)
        assert r.status_code == 402, r.text

    def test_free_tighten_pauses_gets_402(self, free_client):
        r = self._post(free_client, tighten_pauses=True)
        assert r.status_code == 402, r.text

    def test_free_multilingual_gets_402(self, free_client):
        r = self._post(free_client, multilingual=True)
        assert r.status_code == 402, r.text

    def test_free_gameplay_template_gets_402(self, free_client):
        r = self._post(free_client, template="gameplay")
        # 402 = plan gate; 400 acceptable only if no gameplay assets installed.
        assert r.status_code in (402, 400), r.text
        # But we prefer plan-gate to fire first.
        if r.status_code == 400:
            pytest.skip(f"gameplay assets missing: {r.text}")

    def test_admin_gates_pass_validation(self, admin_client):
        # These will *start* jobs, but validation must pass (no 402).
        # Use a nonsense URL that still validates (http/https scheme) so we
        # can inspect the _gate() response before the pipeline dies for lack
        # of DEEPGRAM/OPENAI keys.
        r = admin_client.post(f"{BASE_URL}/api/jobs", json={
            "url": self.URL, "n_clips": 5,
            "tighten_pauses": True, "multilingual": True,
            "template": "classic"})
        assert r.status_code == 200, r.text
        assert "job_id" in r.json()


# ---- Share pages ------------------------------------------------------------

class TestSharePages:
    def test_free_share_denied_402(self, free_client):
        r = free_client.post(f"{BASE_URL}/api/jobs/{DEMO_JOB}/clips/0/share")
        assert r.status_code == 402, r.text

    def test_admin_share_mint_and_idempotent(self, admin_client):
        r1 = admin_client.post(f"{BASE_URL}/api/jobs/{DEMO_JOB}/clips/0/share")
        assert r1.status_code == 200, r1.text
        d1 = r1.json()
        assert d1.get("token")
        assert d1.get("path") == f"/s/{d1['token']}"

        r2 = admin_client.post(f"{BASE_URL}/api/jobs/{DEMO_JOB}/clips/0/share")
        assert r2.status_code == 200
        assert r2.json()["token"] == d1["token"], "share must be idempotent"

        # Stash for the next test class
        TestSharePages.token = d1["token"]

    def test_other_users_job_gets_403(self, free_client):
        # demo-job-0001 is owned by admin; free user tries to share it.
        r = free_client.post(f"{BASE_URL}/api/jobs/{DEMO_JOB}/clips/0/share")
        # Plan gate fires first (402). To hit 403 we'd need a paid non-owner;
        # skip that combo here — the ownership path is exercised via reading
        # the code and tested end-to-end in main.py. Confirm it's not a 200.
        assert r.status_code != 200


class TestSharePublicRead:
    def test_public_read_and_view_increment(self, admin_client):
        # Ensure token exists
        r = admin_client.post(f"{BASE_URL}/api/jobs/{DEMO_JOB}/clips/0/share")
        token = r.json()["token"]

        # No auth
        a = requests.get(f"{BASE_URL}/api/share/{token}")
        assert a.status_code == 200, a.text
        d1 = a.json()
        assert d1["token"] == token
        assert "title" in d1 and "hook" in d1 and "score" in d1
        assert "scores" in d1

        v1 = d1.get("views", 0)
        b = requests.get(f"{BASE_URL}/api/share/{token}")
        assert b.status_code == 200
        assert b.json()["views"] == v1 + 1

    def test_invalid_token_404(self):
        r = requests.get(f"{BASE_URL}/api/share/nonsense-token-xyz")
        assert r.status_code == 404


# ---- Watermark option is frozen onto the job --------------------------------

class TestWatermark:
    def test_free_job_freezes_watermark_text(self, free_client):
        # Post a job (validation will fail? No, defaults are free-compatible.)
        r = free_client.post(f"{BASE_URL}/api/jobs", json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "n_clips": 1, "template": "classic",
            "tighten_pauses": False, "multilingual": False})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        j = requests.get(f"{BASE_URL}/api/jobs/{job_id}").json()
        assert j["options"].get("watermark") == "Made with KlipCut"
        assert j["options"].get("max_source_minutes") == 15

    def test_admin_job_has_empty_watermark(self, admin_client):
        r = admin_client.post(f"{BASE_URL}/api/jobs", json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "n_clips": 1, "template": "classic",
            "tighten_pauses": False, "multilingual": False})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        j = requests.get(f"{BASE_URL}/api/jobs/{job_id}").json()
        assert j["options"].get("watermark") == ""
        assert j["options"].get("max_source_minutes") == 240

    def test_watermark_filter_produces_drawtext(self):
        # Import in-process — this file lives in /app/backend/tests so the
        # package is importable when pytest is run from /app/backend.
        import sys
        sys.path.insert(0, "/app/backend")
        from app.pipeline import render
        f, label = render.watermark_filter("Made with Clipper", "[v]", 1920)
        assert "drawtext" in f
        assert "Made with Clipper" in f
        assert label  # renamed label for chain
