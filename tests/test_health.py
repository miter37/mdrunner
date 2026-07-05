from mdrunner.health import probe_health_interactive

def test_probe_health_interactive_missing():
    steps = []
    def progress(msg):
        steps.append(msg)
    
    r = probe_health_interactive("agy", "nonexistent_binary", "model", progress)
    assert not r.ok
    assert any("nonexistent_binary" in s for s in steps)
