"""Tests for issue #538 — MCP server management API."""
import json, pytest
from unittest.mock import patch, MagicMock, call
from api.routes import (
    _handle_mcp_servers_list,
    _handle_mcp_server_update,
    _handle_mcp_server_delete,
    _handle_mcp_server_toggle,
    _handle_mcp_server_test,
    _handle_mcp_presets,
    _handle_mcp_logs,
    _mask_secrets,
    _parse_mcp_enabled,
    _server_summary,
    _strip_masked_values,
)


def _make_handler():
    h = MagicMock()
    h.path = '/api/mcp/servers'
    h.command = 'GET'
    return h


def _json_payload(handler):
    body = handler.wfile.write.call_args[0][0]
    return json.loads(body.decode('utf-8'))


SAMPLE_MCP = {
    "searxng": {
        "command": "mcp-searxng",
        "args": ["--port", "8888"],
        "timeout": 120
    },
    "web-reader": {
        "url": "http://localhost:3001/mcp",
        "timeout": 60,
        "headers": {"Authorization": "Bearer secret123"}
    }
}


@pytest.mark.parametrize(
    ("operation", "stale_config"),
    (
        ("delete", {"mcp_servers": {"target": {"command": "old"}}}),
        ("toggle", {"mcp_servers": {"target": {"command": "old"}}}),
        ("update", {"mcp_servers": {}}),
    ),
)
def test_mcp_mutators_ignore_stale_cache_and_fail_closed_on_malformed_disk_config(
    tmp_path,
    monkeypatch,
    operation,
    stale_config,
):
    from api import routes

    config_path = tmp_path / "config.yaml"
    malformed_payload = b"mcp_servers:\n  target: [unterminated\n"
    config_path.write_bytes(malformed_payload)
    monkeypatch.setattr(routes, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(routes, "get_config", lambda: stale_config)
    monkeypatch.setattr(routes, "reload_config", lambda: None)

    handler = _make_handler()
    if operation == "delete":
        invoke = lambda: routes._handle_mcp_server_delete(handler, "target")
    elif operation == "toggle":
        invoke = lambda: routes._handle_mcp_server_toggle(
            handler,
            "target",
            {"enabled": False},
        )
    else:
        invoke = lambda: routes._handle_mcp_server_update(
            handler,
            "target",
            {"command": "new"},
        )

    with pytest.raises(ValueError, match="config"):
        invoke()

    assert config_path.read_bytes() == malformed_payload


class TestMcpList:
    """GET /api/mcp/servers — list with masked secrets."""

    @patch('api.routes.get_config')
    def test_returns_servers_list(self, mock_cfg):
        mock_cfg.return_value = {'mcp_servers': SAMPLE_MCP}
        h = _make_handler()
        _handle_mcp_servers_list(h)
        assert h.send_response.called
        status = h.send_response.call_args[0][0]
        assert status == 200

    @patch('api.routes.get_config')
    def test_empty_config(self, mock_cfg):
        mock_cfg.return_value = {}
        h = _make_handler()
        _handle_mcp_servers_list(h)
        assert h.send_response.called
        status = h.send_response.call_args[0][0]
        assert status == 200
        payload = _json_payload(h)
        assert payload['servers'] == []
        assert payload['toggle_supported'] is True
        assert payload['reload_required'] is True

    @patch('api.routes._mcp_runtime_status_by_name')
    @patch('api.routes.get_config')
    def test_list_payload_includes_status_tool_counts_and_safe_invalid_config(self, mock_cfg, mock_runtime):
        mock_cfg.return_value = {
            'mcp_servers': {
                'searxng': {'command': 'mcp-searxng', 'args': ['--port', '8888']},
                'web-reader': {
                    'url': 'http://localhost:3001/mcp',
                    'headers': {'Authorization': 'Bearer secret123'},
                },
                'disabled': {'command': 'disabled-cmd', 'enabled': 0},
                'broken': 'not-a-dict',
            }
        }
        mock_runtime.return_value = {
            'searxng': {'connected': True, 'tools': 3},
            'web-reader': {'connected': False, 'tools': 0},
        }
        h = _make_handler()
        _handle_mcp_servers_list(h)
        payload = _json_payload(h)
        by_name = {s['name']: s for s in payload['servers']}
        assert by_name['searxng']['status'] == 'active'
        assert by_name['searxng']['active'] is True
        assert by_name['searxng']['tool_count'] == 3
        assert by_name['web-reader']['status'] == 'configured'
        assert '••••' in by_name['web-reader']['headers']['Authorization']
        assert by_name['disabled']['enabled'] is False
        assert by_name['disabled']['active'] is False
        assert by_name['disabled']['status'] == 'disabled'
        assert by_name['broken']['transport'] == 'invalid'
        assert by_name['broken']['status'] == 'invalid_config'

    def test_secrets_are_masked(self):
        """_mask_secrets hides API keys in headers and env."""
        masked = _mask_secrets(SAMPLE_MCP['web-reader']['headers'])
        assert masked['Authorization'] != 'Bearer secret123'
        assert '••••' in masked['Authorization']

    def test_server_summary_stdio(self):
        summary = _server_summary('searxng', SAMPLE_MCP['searxng'])
        assert summary['transport'] == 'stdio'
        assert summary['command'] == 'mcp-searxng'
        assert summary['args'] == ['--port', '8888']

    def test_server_summary_http(self):
        summary = _server_summary('web-reader', SAMPLE_MCP['web-reader'])
        assert summary['transport'] == 'http'
        assert summary['url'] == 'http://localhost:3001/mcp'
        assert '••••' in summary['headers']['Authorization']

    def test_server_summary_default_timeout(self):
        summary = _server_summary('minimal', {'command': 'x'})
        assert summary['timeout'] == 120

    def test_numeric_zero_enabled_flag_is_disabled(self):
        """YAML numeric false-y values should not show a disabled server as enabled."""
        assert _parse_mcp_enabled(0) is False


class TestMcpSave:
    """PUT /api/mcp/servers/<name> — add or update."""

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_add_new_stdio_server(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {}
        h = _make_handler()
        h.command = 'PUT'
        body = {"command": "test-cmd", "timeout": 30}
        _handle_mcp_server_update(h, 'test-server', body)
        assert mock_save.called
        saved = mock_save.call_args[0][1]
        assert 'test-server' in saved['mcp_servers']
        assert saved['mcp_servers']['test-server']['command'] == 'test-cmd'

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_add_new_http_server(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {}
        h = _make_handler()
        h.command = 'PUT'
        body = {"url": "http://localhost:4000", "timeout": 60}
        _handle_mcp_server_update(h, 'http-srv', body)
        saved = mock_save.call_args[0][1]
        assert saved['mcp_servers']['http-srv']['url'] == 'http://localhost:4000'

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_update_existing(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'existing': {'command': 'old'}}}
        h = _make_handler()
        h.command = 'PUT'
        body = {"command": "new-cmd"}
        _handle_mcp_server_update(h, 'existing', body)
        saved = mock_save.call_args[0][1]
        assert saved['mcp_servers']['existing']['command'] == 'new-cmd'

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_preserves_other_servers(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'keep': {'command': 'stay'}}}
        h = _make_handler()
        h.command = 'PUT'
        body = {"command": "new"}
        _handle_mcp_server_update(h, 'add-me', body)
        saved = mock_save.call_args[0][1]
        assert 'keep' in saved['mcp_servers']
        assert 'add-me' in saved['mcp_servers']

    def test_empty_name_rejected(self):
        h = _make_handler()
        h.command = 'PUT'
        _handle_mcp_server_update(h, '', {"command": "test"})
        assert h.send_response.called
        status = h.send_response.call_args[0][0]
        assert status == 400

    def test_missing_command_and_url_rejected(self):
        h = _make_handler()
        h.command = 'PUT'
        _handle_mcp_server_update(h, 'test', {"timeout": 30})
        assert h.send_response.called
        status = h.send_response.call_args[0][0]
        assert status == 400

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_filesystem_preset_expands_to_authorized_directory_config(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {}
        h = _make_handler()
        h.command = 'PUT'
        body = {"preset": "filesystem", "allowed_roots": ["/tmp/hermes-mcp-demo"], "timeout": 30}
        _handle_mcp_server_update(h, 'filesystem', body)
        saved = mock_save.call_args[0][1]
        cfg = saved['mcp_servers']['filesystem']
        assert cfg['command'] == 'npx'
        assert cfg['args'] == ['-y', '@modelcontextprotocol/server-filesystem', '/tmp/hermes-mcp-demo']
        assert cfg['allowed_roots'] == ['/tmp/hermes-mcp-demo']

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_playwright_preset_expands_to_stdio_config(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {}
        h = _make_handler()
        h.command = 'PUT'
        body = {"preset": "playwright", "headless": True, "timeout": 60}
        _handle_mcp_server_update(h, 'playwright', body)
        saved = mock_save.call_args[0][1]
        cfg = saved['mcp_servers']['playwright']
        assert cfg['command'] == 'npx'
        assert cfg['args'] == ['@playwright/mcp@latest', '--headless']
        assert cfg['browser_actions_require_confirmation'] is True


class TestMcpPresetsAndProbe:
    def test_presets_endpoint_lists_filesystem_and_playwright_examples(self):
        h = _make_handler()
        _handle_mcp_presets(h)
        payload = _json_payload(h)
        names = {preset['id'] for preset in payload['presets']}
        assert {'filesystem', 'playwright'} <= names
        fs = next(p for p in payload['presets'] if p['id'] == 'filesystem')
        assert fs['requires_allowed_roots'] is True
        assert 'authorized directories' in fs['security_note'].lower()

    @patch('api.routes._probe_mcp_server_tools')
    @patch('api.routes.get_config')
    def test_test_endpoint_probes_configured_server_and_returns_tools(self, mock_cfg, mock_probe):
        mock_cfg.return_value = {'mcp_servers': {'filesystem': {'command': 'npx', 'args': ['-y', '@modelcontextprotocol/server-filesystem', '/tmp/demo']}}}
        mock_probe.return_value = [('read_file', 'Read a file')]
        h = _make_handler()
        h.command = 'POST'
        _handle_mcp_server_test(h, 'filesystem', {})
        payload = _json_payload(h)
        assert payload['ok'] is True
        assert payload['tool_count'] == 1
        assert payload['tools'][0]['name'] == 'read_file'

    @patch('api.routes._probe_mcp_server_tools')
    @patch('api.routes.get_config')
    def test_test_endpoint_probes_submitted_form_config_without_saved_server(self, mock_cfg, mock_probe):
        mock_cfg.return_value = {'mcp_servers': {}}
        mock_probe.return_value = [('browser_navigate', 'Navigate')]
        h = _make_handler()
        h.command = 'POST'
        _handle_mcp_server_test(h, 'playwright', {"preset": "playwright", "headless": True})
        payload = _json_payload(h)
        assert payload['ok'] is True
        assert payload['tool_count'] == 1
        mock_probe.assert_called_once()
        name, config = mock_probe.call_args[0][:2]
        assert name == 'playwright'
        assert config['command'] == 'npx'
        assert config['args'] == ['@playwright/mcp@latest', '--headless']

    @patch('api.routes._probe_mcp_server_tools', side_effect=RuntimeError('token=secret failed'))
    @patch('api.routes.get_config')
    def test_test_endpoint_redacts_probe_failures(self, mock_cfg, mock_probe):
        mock_cfg.return_value = {'mcp_servers': {'bad': {'command': 'missing'}}}
        h = _make_handler()
        h.command = 'POST'
        _handle_mcp_server_test(h, 'bad', {})
        payload = _json_payload(h)
        assert payload['ok'] is False
        assert 'secret' not in payload['error']


class TestMcpLogs:
    def test_logs_endpoint_returns_recent_jsonl_entries(self, tmp_path, monkeypatch):
        log_path = tmp_path / 'mcp-tool-calls.jsonl'
        log_path.write_text(
            '{"ts":"2026-06-08T01:00:00Z","server":"filesystem","tool":"read_file","ok":true}\n'
            '{"bad":\n'
            '{"ts":"2026-06-08T01:01:00Z","server":"playwright","tool":"browser_navigate","ok":false,"error":"[REDACTED]"}\n',
            encoding='utf-8',
        )
        monkeypatch.setattr('api.routes._mcp_tool_call_log_path', lambda: log_path)
        h = _make_handler()
        _handle_mcp_logs(h)
        payload = _json_payload(h)
        assert payload['entries'][0]['server'] == 'playwright'
        assert payload['entries'][1]['server'] == 'filesystem'
        assert payload['skipped_invalid'] == 1


class TestMcpDelete:
    """DELETE /api/mcp/servers/<name>."""

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_delete_existing(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'target': {'command': 'rm'}}}
        h = _make_handler()
        h.command = 'DELETE'
        _handle_mcp_server_delete(h, 'target')
        assert mock_save.called
        saved = mock_save.call_args[0][1]
        assert 'target' not in saved.get('mcp_servers', {})

    @patch('api.routes._load_yaml_config_file_strict')
    def test_delete_nonexistent(self, mock_load):
        mock_load.return_value = {'mcp_servers': {}}
        h = _make_handler()
        h.command = 'DELETE'
        _handle_mcp_server_delete(h, 'ghost')
        status = h.send_response.call_args[0][0]
        assert status == 404

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_preserves_others(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'a': {'c': '1'}, 'b': {'c': '2'}}}
        h = _make_handler()
        h.command = 'DELETE'
        _handle_mcp_server_delete(h, 'a')
        saved = mock_save.call_args[0][1]
        assert 'a' not in saved['mcp_servers']
        assert 'b' in saved['mcp_servers']

    def test_empty_name_rejected(self):
        h = _make_handler()
        h.command = 'DELETE'
        _handle_mcp_server_delete(h, '')
        status = h.send_response.call_args[0][0]
        assert status == 400


class TestMaskSecrets:
    """Unit tests for _mask_secrets helper."""

    def test_masks_env_values(self):
        obj = {"env": {"API_KEY": "***", "PUBLIC_VAR": "visible"}}
        result = _mask_secrets(obj)
        assert result["env"]["API_KEY"] == "••••••"
        assert result["env"]["PUBLIC_VAR"] == "visible"

    def test_masks_headers(self):
        obj = {"headers": {"Authorization": "Bearer token", "Accept": "application/json"}}
        result = _mask_secrets(obj)
        assert "••••" in result["headers"]["Authorization"]
        assert result["headers"]["Accept"] == "application/json"

    def test_passes_non_dict(self):
        assert _mask_secrets("hello") == "hello"
        assert _mask_secrets(42) == 42
        assert _mask_secrets(None) is None

    def test_handles_empty_dict(self):
        assert _mask_secrets({}) == {}

    def test_masks_password_key(self):
        obj = {"password": "hunter2"}
        result = _mask_secrets(obj)
        assert result["password"] == "••••••"


class TestStripMaskedValues:
    """Unit tests for _strip_masked_values helper (secret round-trip protection)."""

    def test_masked_env_preserves_original(self):
        """Submitting masked env value should keep the original stored value."""
        existing = {"API_KEY": "real-secret-123", "PUBLIC": "visible"}
        submitted = {"API_KEY": "••••••", "PUBLIC": "updated"}
        result = _strip_masked_values(submitted, existing)
        assert result["API_KEY"] == "real-secret-123"
        assert result["PUBLIC"] == "updated"

    def test_masked_headers_preserves_original(self):
        """Submitting masked header value should keep the original stored value."""
        existing = {"Authorization": "Bearer token123", "Accept": "application/json"}
        submitted = {"Authorization": "••••••", "Accept": "text/html"}
        result = _strip_masked_values(submitted, existing)
        assert result["Authorization"] == "Bearer token123"
        assert result["Accept"] == "text/html"

    def test_new_key_still_saved(self):
        """New keys (not in existing) should be saved even if they look sensitive."""
        existing = {"OLD_KEY": "old"}
        submitted = {"NEW_KEY": "new-value", "OLD_KEY": "••••••"}
        result = _strip_masked_values(submitted, existing)
        assert result["OLD_KEY"] == "old"
        assert result["NEW_KEY"] == "new-value"

    def test_non_dict_passthrough(self):
        assert _strip_masked_values("hello", {}) == "hello"
        assert _strip_masked_values(42, {}) == 42

    def test_empty_dicts(self):
        assert _strip_masked_values({}, {}) == {}
        assert _strip_masked_values({"k": "v"}, {}) == {"k": "v"}


class TestMcpToggle:
    """PATCH /api/mcp/servers/<name> — enable/disable."""

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_disable_server(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'myserver': {'command': 'run'}}}
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'myserver', {'enabled': False})
        assert mock_save.called
        saved = mock_save.call_args[0][1]
        assert saved['mcp_servers']['myserver']['enabled'] is False
        assert mock_reload.called

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_enable_server(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'myserver': {'command': 'run', 'enabled': False}}}
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'myserver', {'enabled': True})
        saved = mock_save.call_args[0][1]
        assert saved['mcp_servers']['myserver']['enabled'] is True

    @patch('api.routes._load_yaml_config_file_strict')
    def test_nonexistent_server_returns_404(self, mock_load):
        mock_load.return_value = {'mcp_servers': {}}
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'ghost', {'enabled': True})
        status = h.send_response.call_args[0][0]
        assert status == 404

    def test_empty_name_rejected(self):
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, '', {'enabled': True})
        status = h.send_response.call_args[0][0]
        assert status == 400

    def test_missing_enabled_field_rejected(self):
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'myserver', {})
        status = h.send_response.call_args[0][0]
        assert status == 400

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_response_payload(self, mock_load, mock_path, mock_save, mock_reload):
        mock_load.return_value = {'mcp_servers': {'srv': {'url': 'http://localhost'}}}
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'srv', {'enabled': False})
        body = h.wfile.write.call_args[0][0]
        payload = json.loads(body.decode('utf-8'))
        assert payload == {'ok': True, 'name': 'srv', 'enabled': False}

    @patch('api.routes.reload_config')
    @patch('api.routes._save_yaml_config_file')
    @patch('api.routes._get_config_path', return_value='/tmp/test.yaml')
    @patch('api.routes._load_yaml_config_file_strict')
    def test_url_encoded_name(self, mock_load, mock_path, mock_save, mock_reload):
        """Names with special characters must be URL-decoded."""
        mock_load.return_value = {'mcp_servers': {'my server': {'command': 'x'}}}
        h = _make_handler()
        h.command = 'PATCH'
        _handle_mcp_server_toggle(h, 'my%20server', {'enabled': False})
        saved = mock_save.call_args[0][1]
        assert 'my server' in saved['mcp_servers']
        assert saved['mcp_servers']['my server']['enabled'] is False
