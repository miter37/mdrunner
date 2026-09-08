from unittest.mock import patch, MagicMock
from mdrunner.telegram import send_document

def test_send_document_missing_config():
    ok, err = send_document(bot_token="", chat_id="", file_path="dummy.md")
    assert not ok
    assert "not configured" in err

def test_send_document_file_not_found():
    ok, err = send_document(bot_token="bot123", chat_id="chat456", file_path="non_existent_file_xyz.md")
    assert not ok
    assert "file not found" in err

def test_send_document_success(tmp_path):
    file_path = tmp_path / "test.md"
    file_path.write_text("Hello, Telegram!", encoding="utf-8")

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = b'{"ok": true, "result": {"message_id": 123}}'

    with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        ok, res = send_document(
            bot_token="token123",
            chat_id="chat456",
            file_path=str(file_path),
            caption="Test Caption"
        )
        
        assert ok
        assert '{"ok": true' in res
        
        # Verify urlopen was called once
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        
        # Check Request attributes
        assert req.full_url == "https://api.telegram.org/bottoken123/sendDocument"
        assert req.method == "POST"
        
        # Check headers
        content_type = req.get_header("Content-type")
        assert content_type.startswith("multipart/form-data; boundary=")
        boundary = content_type.split("boundary=")[1]
        
        assert req.get_header("Content-length") == str(len(req.data))
        
        # Check body structure
        body = req.data
        assert b"chat_id" in body
        assert b"chat456" in body
        assert b"caption" in body
        assert b"Test Caption" in body
        assert b"document" in body
        assert f'filename="{file_path.name}"'.encode() in body
        assert b"Hello, Telegram!" in body
        assert f"--{boundary}".encode() in body
        assert f"--{boundary}--".encode() in body

def test_send_document_http_error(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("dummy", encoding="utf-8")
    
    with patch("urllib.request.urlopen", side_effect=Exception("HTTP Error 400: Bad Request")):
        ok, err = send_document(
            bot_token="token123",
            chat_id="chat456",
            file_path=str(file_path),
        )
        assert not ok
        assert "HTTP Error 400" in err


def test_send_document_json_failure_is_not_success(tmp_path):
    file_path = tmp_path / "test.txt"
    file_path.write_text("dummy", encoding="utf-8")

    mock_response = MagicMock()
    mock_response.__enter__.return_value = mock_response
    mock_response.read.return_value = (
        b'{"ok": false, "error_code": 403, "description": "bot was blocked"}'
    )

    with patch("urllib.request.urlopen", return_value=mock_response):
        ok, err = send_document(
            bot_token="token123",
            chat_id="chat456",
            file_path=str(file_path),
        )

    assert not ok
    assert "bot was blocked" in err


def test_telegram_settings_saved_owner_only(monkeypatch, tmp_path):
    import os
    import stat as statmod

    monkeypatch.setenv("RUNCHER_CONFIG_DIR", str(tmp_path))
    from mdrunner.ui import _telegram_settings

    _telegram_settings.save({"bot_token": "t", "chat_id": "c"})
    p = _telegram_settings.path()
    assert p.exists()
    if os.name == "posix":
        assert statmod.S_IMODE(p.stat().st_mode) == 0o600
