"""BE-09: LLM proposals. The client is faked; no network."""

from types import SimpleNamespace

import anthropic
import httpx2 as httpx  # the SDK's own HTTP client
import pytest
from sqlalchemy import select

from app import llm
from app import models as m
from app.cli import load_school
from app.models import db
from tools.seed_to_contract import SEED, load_seed


@pytest.fixture
def admin(make_client, migrated):
    load_school(load_seed(SEED.parent / "tiny_school.json"))
    db.session.commit()
    migrated.config.update(LLM_ENABLED=True, ANTHROPIC_API_KEY="test-key")
    return make_client("admin")


def fake(monkeypatch, out=None, error=None, stop_reason="tool_use"):
    calls = []

    def create(**kw):
        calls.append(kw)
        if error:
            raise error
        return SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="tool_use", name=llm.TOOL, input=out)],
        )

    monkeypatch.setattr(
        llm, "_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create))
    )
    return calls


def days(teacher_id):
    return {
        "type": "teacher_available_days",
        "hard": True,
        "params": {"teacher_id": teacher_id, "days": [5]},
    }


def proposal(c, text="Иванова само в петък"):
    return {"text": text, "constraint": c}


def post(admin, text="Иванова може само в петък."):
    return admin.post(
        "/constraints/propose", data={"text": text}, follow_redirects=True
    )


def saved():
    return db.session.scalars(select(m.Constraint)).all()


def test_valid_proposal_is_saved_as_proposed(admin, monkeypatch):
    teacher = db.session.scalars(select(m.Teacher)).first()
    calls = fake(
        monkeypatch,
        {"constraints": [proposal(days(teacher.id))], "not_understood": []},
    )
    assert post(admin).status_code == 200
    (c,) = saved()
    assert (c.status, c.source, c.original_text) == (
        "proposed",
        "llm",
        "Иванова само в петък",
    )
    assert c.params == {"teacher_id": teacher.id, "days": [5]}
    # tool schema generated from the models, school names sent as a cached block
    kw = calls[0]
    assert "tool_choice" not in kw  # forced tool use is rejected by Sonnet 5.5
    assert "FixedSlot" in kw["tools"][0]["input_schema"]["$defs"]
    school, request = kw["messages"][0]["content"]
    assert teacher.name in school["text"] and school["cache_control"]
    assert "Иванова" in request["text"]


def test_unknown_name_and_invalid_params_are_not_saved(admin, monkeypatch):
    out = {
        "constraints": [
            proposal(days(999999), "Петров само в петък"),  # id not in the school
            proposal({**days(1), "params": {"teacher_id": 1, "days": []}}, "празно"),
        ],
        "not_understood": [{"text": "Сидоров не обича понеделник", "reason": "?"}],
    }
    fake(monkeypatch, out)
    body = post(admin).get_data(as_text=True)
    assert saved() == []
    for fragment in ("Сидоров не обича понеделник", "Петров само в петък", "празно"):
        assert fragment in body


def test_too_long_text_is_refused_before_the_call(admin, monkeypatch):
    calls = fake(monkeypatch, {"constraints": [], "not_understood": []})
    body = post(admin, "а" * (llm.MAX_CHARS + 1)).get_data(as_text=True)
    assert "твърде дълъг" in body and calls == []


def test_disabled_flag_hides_the_route(admin, migrated):
    migrated.config["LLM_ENABLED"] = False
    assert admin.post("/constraints/propose", data={"text": "x"}).status_code == 404


@pytest.mark.parametrize(
    "error",
    [
        anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")),
        anthropic.APITimeoutError(request=httpx.Request("POST", "http://x")),
    ],
)
def test_api_error_flashes_and_saves_nothing(admin, monkeypatch, error):
    fake(monkeypatch, error=error)
    r = post(admin)
    assert r.status_code == 200 and "Услугата не отговори" in r.get_data(as_text=True)
    assert saved() == []


def test_garbage_output_saves_nothing(admin, monkeypatch):
    fake(monkeypatch, {"constraints": "oops"})
    assert "не можа да бъде разчетен" in post(admin).get_data(as_text=True)
    assert saved() == []


def test_rate_limit_per_user_and_daily_cap(admin, migrated, monkeypatch):
    fake(monkeypatch, {"constraints": [], "not_understood": []})
    migrated.config["LLM_RATE_LIMIT"] = "2 per minute"
    codes = [
        admin.post("/constraints/propose", data={"text": "x"}).status_code
        for _ in range(3)
    ]
    assert codes == [302, 302, 429]


def test_daily_cap_is_shared_by_all_admins(admin, migrated, make_client, monkeypatch):
    fake(monkeypatch, {"constraints": [], "not_understood": []})
    migrated.config["LLM_DAILY_CAP"] = "1 per day"
    assert admin.post("/constraints/propose", data={"text": "x"}).status_code == 302
    assert admin.post("/constraints/propose", data={"text": "x"}).status_code == 429


@pytest.mark.parametrize(
    ("stop_reason", "message"),
    [("max_tokens", "твърде голяма"), ("refusal", "не може да бъде обработена")],
)
def test_cut_off_or_refused_reply_saves_nothing(
    admin, monkeypatch, stop_reason, message
):
    # A reply cut at max_tokens carries a truncated (often empty) tool input.
    calls = fake(monkeypatch, {}, stop_reason=stop_reason)
    assert message in post(admin).get_data(as_text=True)
    assert saved() == []
    assert calls[0]["max_tokens"] == llm.MAX_OUTPUT_TOKENS
