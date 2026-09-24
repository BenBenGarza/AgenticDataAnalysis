from dataclasses import replace

import anthropic
import httpx
import pytest

from app.agent import GENERIC_FAILURE, Agent, describe_api_error
from app.config import Settings
from app.events import TextDelta, ToolFinished, ToolStarted, TurnFailed, TurnFinished
from app.tools.base import ToolUnavailable
from tests.fakes import FakeAnthropic, FakeTool, reply, text, tool_use

SETTINGS = Settings(gcp_project="test", max_agent_steps=3)
SYSTEM_PROMPT = "You are a test analyst."


def _agent(client: FakeAnthropic, *tools: FakeTool, messages=None) -> Agent:
    return Agent(SETTINGS, client, tools or [FakeTool()], SYSTEM_PROMPT, messages)


def _run(agent: Agent, question: str = "question") -> list:
    return list(agent.ask(question))


def test_plain_answer_streams_text_and_records_the_turn():
    agent = _agent(FakeAnthropic(reply(text("Revenue was $10."))))

    events = _run(agent)

    assert events[0] == TextDelta("Revenue was $10.")
    finished = events[-1]
    assert isinstance(finished, TurnFinished)
    assert finished.usage.model_calls == 1 and finished.usage.cache_read_tokens == 100
    assert (
        finished.new_messages
        == agent.messages
        == [
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "text", "text": "Revenue was $10."},
                ],
            },
        ]
    )


def test_request_carries_system_prompt_tools_and_history():
    client = FakeAnthropic(reply(text("ok")))
    tool = FakeTool("lookup")

    _run(
        _agent(
            client,
            tool,
            messages=[
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": [text("before")]},
            ],
        )
    )

    request = client.requests[0]
    assert request["system"][0]["text"] == SYSTEM_PROMPT
    assert request["tools"] == [tool.definition]
    assert request["model"] == SETTINGS.model
    assert [m["content"] for m in request["messages"]] == ["earlier", [text("before")], "question"]


def test_tool_call_result_is_sent_back_and_the_loop_continues():
    client = FakeAnthropic(
        reply(tool_use("t1", "lookup", {"key": "a"})),
        reply(text("The answer is a.")),
    )
    tool = FakeTool()
    agent = _agent(client, tool)

    events = _run(agent)

    assert tool.calls == [{"key": "a"}]
    assert events[0] == ToolStarted("t1", "lookup", {"key": "a"})
    assert events[1] == ToolFinished("t1", "lookup", output={"key": "a"})
    assert isinstance(events[-1], TurnFinished) and len(events[-1].new_messages) == 4
    assert client.requests[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "result for a"},
        ],
    }


def test_tool_errors_are_returned_to_the_model_to_recover():
    client = FakeAnthropic(
        reply(tool_use("t1", "lookup", {"key": "bad"})),
        reply(tool_use("t2", "lookup", {"key": "good"})),
        reply(text("Fixed it.")),
    )
    agent = _agent(client, FakeTool(errors={"bad": "Unrecognized name: evnt_name"}))

    events = _run(agent)

    assert ToolFinished("t1", "lookup", error="Unrecognized name: evnt_name") in events
    assert client.requests[1]["messages"][-1]["content"] == [
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "Unrecognized name: evnt_name",
            "is_error": True,
        },
    ]
    assert isinstance(events[-1], TurnFinished)


def test_unknown_tool_is_reported_as_an_error_result():
    client = FakeAnthropic(reply(tool_use("t1", "no_such_tool", {})), reply(text("ok")))

    events = _run(_agent(client))

    assert ToolFinished("t1", "no_such_tool", error="Unknown tool: no_such_tool") in events


def test_parallel_tool_calls_return_all_results_in_one_message_in_order():
    client = FakeAnthropic(
        reply(tool_use("t1", "lookup", {"key": "a"}), tool_use("t2", "lookup", {"key": "b"})),
        reply(text("done")),
    )

    _run(_agent(client))

    assert client.requests[1]["messages"][-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "result for a"},
        {"type": "tool_result", "tool_use_id": "t2", "content": "result for b"},
    ]


@pytest.mark.parametrize(
    "failing_step, expected",
    [
        (reply(text("..."), stop_reason="refusal"), "declined"),
        (reply(text("..."), stop_reason="max_tokens"), "output token limit"),
        (
            anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.com")
            ),
            "Couldn't reach the Claude API",
        ),
    ],
)
def test_failed_turn_rolls_history_back(failing_step, expected):
    earlier = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": [text("ok")]},
    ]
    client = FakeAnthropic(reply(tool_use("t1", "lookup", {"key": "a"})), failing_step)
    agent = _agent(client, messages=earlier)

    events = _run(agent)

    assert isinstance(events[-1], TurnFailed) and expected in events[-1].message
    assert agent.messages == earlier  # the partial turn (question + tool call + result) is gone


def test_step_limit_stops_a_turn_that_never_answers():
    client = FakeAnthropic(*(reply(tool_use(f"t{i}", "lookup", {"key": "a"})) for i in range(3)))
    agent = _agent(client)

    events = _run(agent)

    assert isinstance(events[-1], TurnFailed) and "Stopped after 3 steps" in events[-1].message
    assert agent.messages == []


def test_abandoning_a_turn_midway_rolls_it_back():
    client = FakeAnthropic(reply(tool_use("t1", "lookup", {"key": "a"})), reply(text("never read")))
    agent = _agent(client)

    events = agent.ask("question")
    assert isinstance(next(events), ToolStarted)
    events.close()  # e.g. the browser disconnected

    assert agent.messages == []


def test_next_question_continues_the_same_history():
    client = FakeAnthropic(reply(text("first answer")), reply(text("second answer")))
    agent = _agent(client)

    _run(agent, "Q1")
    _run(agent, "Q2")

    assert [m["content"] for m in client.requests[1]["messages"]] == [
        "Q1",
        [{"type": "thinking", "thinking": "", "signature": "sig"}, text("first answer")],
        "Q2",
    ]


def test_effort_setting_is_sent():
    client = FakeAnthropic(reply(text("ok")))
    _run(Agent(replace(SETTINGS, effort="low"), client, [FakeTool()], SYSTEM_PROMPT))
    assert client.requests[0]["output_config"] == {"effort": "low"}


def test_tools_receive_their_call_id_and_earlier_successful_results():
    client = FakeAnthropic(
        reply(tool_use("t1", "lookup", {"key": "a"}), tool_use("t2", "lookup", {"key": "bad"})),
        reply(tool_use("t3", "lookup", {"key": "b"})),
        reply(text("done")),
    )
    tool = FakeTool(errors={"bad": "failed"})

    _run(_agent(client, tool))

    first, _, third = tool.contexts
    assert first.tool_use_id == "t1" and first.results == {}
    assert third.tool_use_id == "t3"
    assert third.results == {"t1": "result for a"}  # the failed t2 is not offered


def test_stale_thinking_is_dropped_rather_than_rejected():
    client = FakeAnthropic(reply(text("ok")))
    _run(_agent(client))
    request = client.requests[0]
    assert request["thinking"]["block_binding"] == {"prefix_mismatch_behavior": "drop_block"}
    assert "thinking-binding-controls-2026-08-01" in request["betas"]


def _api_error(cls, status: int, message: str = "error"):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    "error, expected",
    [
        (_api_error(anthropic.AuthenticationError, 401), "API key was rejected"),
        (
            _api_error(anthropic.BadRequestError, 400, "Your credit balance is too low"),
            "out of credit",
        ),
        (_api_error(anthropic.RateLimitError, 429), "Too many requests"),
        (_api_error(anthropic.OverloadedError, 529), "temporarily unavailable"),
        (_api_error(anthropic.InternalServerError, 500), "temporarily unavailable"),
        (_api_error(anthropic.APIStatusError, 418), "returned an error (418)"),
        (
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com")),
            "Couldn't reach the Claude API",
        ),
    ],
)
def test_api_errors_become_actionable_messages(error, expected):
    assert expected in describe_api_error(error)


class _BrokenTool(FakeTool):
    def __init__(self, error: Exception):
        super().__init__()
        self._error = error

    def run(self, tool_input, context):
        raise self._error


@pytest.mark.parametrize(
    "error, expected",
    [
        (ToolUnavailable("Google Cloud credentials are missing or expired"), "credentials"),
        (RuntimeError("bug"), GENERIC_FAILURE),  # details go to the log, not the user
    ],
)
def test_tools_that_cannot_run_fail_the_turn_and_roll_back(error, expected):
    client = FakeAnthropic(reply(tool_use("t1", "lookup", {"key": "a"})), reply(text("never")))
    agent = _agent(client, _BrokenTool(error))

    events = _run(agent)

    assert isinstance(events[-1], TurnFailed) and expected in events[-1].message
    assert agent.messages == []
    assert len(client.requests) == 1  # the model wasn't asked to retry something it can't fix


def test_usage_accumulates_tokens_and_estimated_cost():
    client = FakeAnthropic(reply(tool_use("t1", "lookup", {"key": "a"})), reply(text("ok")))

    usage = _run(_agent(client))[-1].usage

    # Each fake reply: 10 input, 100 cache-read, 5 output tokens, at Opus 5.5 list prices.
    per_call = (10 * 4.0 + 100 * 0.20 + 5 * 20.0) / 1_000_000
    assert (usage.model_calls, usage.input_tokens, usage.output_tokens) == (2, 20, 10)
    assert usage.cost_usd == pytest.approx(2 * per_call)


def test_old_tool_results_are_cleared_server_side_in_long_conversations():
    client = FakeAnthropic(reply(text("ok")))
    _run(_agent(client))
    (edit,) = client.requests[0]["context_management"]["edits"]
    assert edit["type"] == "clear_tool_uses_20250919"
    assert "context-management-2025-06-27" in client.requests[0]["betas"]
