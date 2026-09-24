import json

from app.display import ChartBlock, DisplayTurn, TextBlock, to_display_turns


def test_display_turns_rebuild_text_queries_and_charts_in_order():
    rows = [{"day": "2020-12-01", "revenue": 10.0}, {"day": "2020-12-02", "revenue": 12.5}]
    result = json.dumps({"result_id": "t1", "columns": ["day", "revenue"], "rows": rows})
    chart_input = {
        "query_id": "t1",
        "type": "line",
        "title": "Revenue",
        "x": "day",
        "y": ["revenue"],
    }
    messages = [
        {"role": "user", "content": "Q1"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "run_sql",
                    "input": {"query": "SELECT 1", "purpose": "p1"},
                },
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "run_sql",
                    "input": {"query": "SELECT bad", "purpose": "p2"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": result},
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "Invalid SQL",
                    "is_error": True,
                },
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t3", "name": "create_chart", "input": chart_input},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t3", "content": "Chart shown"},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "Final answer."},
            ],
        },
        {"role": "user", "content": "Q2"},
        {"role": "assistant", "content": [{"type": "text", "text": "Direct answer."}]},
    ]

    first, second = to_display_turns(messages)

    assert first.question == "Q1"
    text, query, failed_query, chart, answer = first.blocks
    assert text == TextBlock("Let me check.")
    assert (query.purpose, query.sql, query.result["rows"], query.error) == (
        "p1",
        "SELECT 1",
        rows,
        None,
    )
    assert (failed_query.result, failed_query.error) == (None, "Invalid SQL")
    assert isinstance(chart, ChartBlock)
    assert chart.chart.labels == ["2020-12-01", "2020-12-02"]
    assert chart.chart.series[0].values == [10.0, 12.5]
    assert answer == TextBlock("Final answer.")
    assert second == DisplayTurn("Q2", [TextBlock("Direct answer.")])


def test_display_skips_charts_that_failed():
    messages = [
        {"role": "user", "content": "Q"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "create_chart",
                    "input": {
                        "query_id": "missing",
                        "type": "bar",
                        "title": "t",
                        "x": "a",
                        "y": ["b"],
                    },
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "No result",
                    "is_error": True,
                },
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Answer."}]},
    ]
    assert to_display_turns(messages)[0].blocks == [TextBlock("Answer.")]
