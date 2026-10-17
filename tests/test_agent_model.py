"""The model loop with a scripted stand-in for the OpenAI client: tool calls, the spend cap, and the graph-only fallback.
No network; the tools still run against FalkorDB, so this skips when none is reachable."""

import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from provenance import agent, queries
from provenance.graph import load

CALL = {"id": "c1", "name": "check_substitute", "args": json.dumps({"drug": "cisplatin", "substitute": "carboplatin"})}
ANSWER = "Carboplatin shares Intas FEI 3004011473 with cisplatin."


@pytest.fixture(scope="module", autouse=True)
def loaded():
    try:
        if not queries.run("MATCH (f:Facility {fei: '3004011473'}) RETURN f.fei AS fei"):
            load()
    except Exception:
        pytest.skip("FalkorDB not reachable on PV_FALKORDB_HOST:PV_FALKORDB_PORT")


class FakeOpenAI:
    """Turn 1 asks for check_substitute, turn 2 answers. Records every request it gets."""

    requests: list = []
    fail: Exception | None = None

    def __init__(self, **_):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        FakeOpenAI.requests.append(kw)
        if FakeOpenAI.fail:
            raise FakeOpenAI.fail
        first = len([m for m in kw["messages"] if m["role"] == "tool"]) == 0
        if kw.get("stream"):
            return iter(self._chunks(first))
        call = NS(id=CALL["id"], function=NS(name=CALL["name"], arguments=CALL["args"]))
        msg = NS(content=None if first else ANSWER, refusal=None, tool_calls=[call] if first else None)
        msg.model_dump = lambda **_: {"role": "assistant", "tool_calls": [
            {"id": CALL["id"], "type": "function", "function": {"name": CALL["name"], "arguments": CALL["args"]}}]}
        return NS(choices=[NS(message=msg)])

    @staticmethod
    def _chunks(first):
        def chunk(content=None, tool_calls=None):
            return NS(choices=[NS(delta=NS(content=content, tool_calls=tool_calls))])
        if first:  # arguments arrive split across chunks, as the real API sends them
            half = len(CALL["args"]) // 2
            return [chunk(tool_calls=[NS(index=0, id=CALL["id"], function=NS(name=CALL["name"], arguments=CALL["args"][:half]))]),
                    chunk(tool_calls=[NS(index=0, id=None, function=NS(name=None, arguments=CALL["args"][half:]))]),
                    NS(choices=[])]
        return [chunk(content=w) for w in ANSWER.split(" ")[:1]] + [chunk(content=" " + w) for w in ANSWER.split(" ")[1:]]


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(agent.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(agent.settings, "model_calls_per_day", 200)
    monkeypatch.setitem(agent._budget, "used", 0)
    FakeOpenAI.requests, FakeOpenAI.fail = [], None
    return FakeOpenAI


def test_model_calls_a_tool_then_answers_from_its_rows(fake):
    r = agent.ask("Cisplatin is short. Is carboplatin a safe switch?")
    assert r["mode"] == "live" and r["answer"] == ANSWER
    assert [t["tool"] for t in r["trace"]] == ["check_substitute"]
    tool_msg = next(m for m in fake.requests[1]["messages"] if m["role"] == "tool")
    assert "3004011473" in tool_msg["content"], "the graph rows must reach the model"
    assert all(q["max_completion_tokens"] <= 600 for q in fake.requests)


def test_stream_sends_tool_rows_then_the_answer_in_pieces(fake):
    events = list(agent.ask_stream("Cisplatin is short. Is carboplatin a safe switch?"))
    kinds = [e["type"] for e in events]
    assert kinds.index("tool") < kinds.index("delta") and kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == ANSWER
    assert sum(k == "delta" for k in kinds) > 1
    assert events[-1]["mode"] == "live"


def test_spend_cap_stops_model_calls_and_falls_back(fake, monkeypatch):
    monkeypatch.setattr(agent.settings, "model_calls_per_day", 0)
    r = agent.ask("Cisplatin is short. Is carboplatin a safe switch?")
    assert fake.requests == []
    assert r["mode"] == "graph-only" and "budget" in r["note"]
    assert "3004011473" in r["answer"]


def test_model_outage_answers_from_the_graph(fake):
    fake.fail = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    r = agent.ask("Cisplatin is short. Is carboplatin a safe switch?")
    assert r["mode"] == "graph-only" and "APIConnectionError" in r["note"]
    events = list(agent.ask_stream("Cisplatin is short. Is carboplatin a safe switch?"))
    assert events[0]["type"] in {"step", "reset"} and events[-1]["mode"] == "graph-only"
    assert any(e["type"] == "reset" for e in events)
