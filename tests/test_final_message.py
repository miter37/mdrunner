"""Extract the agent's final user-facing reply from a run's stdout."""

from mdrunner.final_message import extract_final_message, split_telegram_chunks


def test_extracts_markdown_digest_after_progress_chatter():
    stdout = """\
I have initiated the pipeline execution to fetch headlines and will proceed once the data collection finishes.
I am waiting for the pipeline to finish headline gathering.
The pipeline command is running to collect headlines across financial sources. I will proceed with processing as soon as it completes.
[투자자 뉴스 다이제스트 보고서](file:///tmp/digest.html) 생성이 완료되었습니다.

### 📌 핵심 3줄 요약
1. **미국 고용 서프라이즈**
2. **유럽 증시 이탈**
3. **AI 인프라 부채**

-> Saved : outputs/digest.html
Saved: /tmp/digest.html
"""
    msg = extract_final_message(stdout)
    assert msg is not None
    assert "핵심 3줄 요약" in msg
    assert "미국 고용" in msg
    assert "I have initiated" not in msg
    assert "Saved:" not in msg


def test_extracts_last_jsonl_result_field():
    stdout = (
        '{"type":"item.started"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"draft"}}\n'
        '{"type":"result","result":"최종 답변입니다."}\n'
    )
    assert extract_final_message(stdout) == "최종 답변입니다."


def test_extracts_plain_assistant_reply_without_progress():
    stdout = "작업이 끝났습니다.\n차트와 요약을 저장했습니다.\n"
    msg = extract_final_message(stdout)
    assert msg is not None
    assert "작업이 끝났습니다" in msg


def test_empty_or_whitespace_is_none():
    assert extract_final_message("") is None
    assert extract_final_message("   \n\n") is None


def test_strips_ansi_and_trailing_mdrunner_lines():
    stdout = (
        "\x1b[32m완료했습니다.\x1b[0m 요약입니다.\n"
        "[mdrunner] telegram artifact delivery succeeded: /tmp/a.md\n"
    )
    msg = extract_final_message(stdout)
    assert msg is not None
    assert "완료했습니다" in msg
    assert "\x1b" not in msg
    assert "[mdrunner]" not in msg


def test_split_telegram_chunks_respects_limit():
    body = ("문단 A.\n\n" + ("가" * 200) + "\n\n문단 B.\n")
    chunks = split_telegram_chunks(body, limit=80)
    assert len(chunks) >= 2
    assert all(len(c) <= 80 for c in chunks)
    assert "".join(chunks).replace("\n", "")  # nothing exploded
