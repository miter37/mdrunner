from mdrunner.health import probe_health_interactive

def test_probe_health_interactive_missing():
    steps = []
    def progress(msg):
        steps.append(msg)
    
    r = probe_health_interactive("agy", "nonexistent_binary", "model", progress)
    assert not r.ok
    assert any("nonexistent_binary" in s for s in steps)


def test_codex_interactive_health_uses_same_yolo_mode_as_tasks(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = "pong"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Completed()

    monkeypatch.setattr("mdrunner.health.resolve_binary", lambda binary: "/usr/bin/codex")
    monkeypatch.setattr("mdrunner.health.subprocess.run", fake_run)

    result = probe_health_interactive("codex", "codex", "gpt-5.4", lambda _: None)

    assert result.ok
    assert calls[1][0] == ["/usr/bin/codex", "exec", "--yolo", "--model", "gpt-5.4", "-"]
    assert calls[1][1]["input"] == "i say ping, you say pong"
