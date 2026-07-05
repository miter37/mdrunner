from mdrunner.utils.models import fetch_agent_models

def test_fetch_agent_models_static():
    # claude의 정적 앨리어스 테스트
    claude_models = fetch_agent_models("claude")
    assert "sonnet" in claude_models
    assert "opus" in claude_models

    # codex의 정적 모델 테스트
    codex_models = fetch_agent_models("codex")
    assert "gpt-5.5" in codex_models
    assert "gpt-5.4-mini" in codex_models
