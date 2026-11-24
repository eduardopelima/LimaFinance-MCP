import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("render_config", Path(__file__).resolve().parents[2] / "ci" / "render_config.py")
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

FULL = {
    "LF_PREFIX": "minhagrana",
    "CF_WORKERS_SUBDOMAIN": "fulano",
    "GOOGLE_CLIENT_ID": "123.apps.googleusercontent.com",
    "D1_ID_PRODUCTION": "d1-prod",
    "KV_ID_PRODUCTION": "kv-prod",
}


def test_local_only_when_nothing_configured():
    out = rc.render({"LF_PREFIX": "limafinance"}, [])
    assert "env" not in out["auth"] and "env" not in out["mcp"]
    assert out["mcp"]["vars"]["MCP_RESOURCE"] == "http://localhost:8787/mcp"


def test_production_block_uses_prefix_and_subdomain():
    out = rc.render(FULL, ["production"])
    auth, mcp = out["auth"]["env"]["production"], out["mcp"]["env"]["production"]
    assert auth["name"] == "minhagrana-auth" and mcp["name"] == "minhagrana"
    assert auth["vars"]["ISSUER"] == "https://minhagrana-auth.fulano.workers.dev"
    assert auth["vars"]["MCP_RESOURCE"] == mcp["vars"]["MCP_RESOURCE"] == "https://minhagrana.fulano.workers.dev/mcp"
    assert mcp["vars"]["AUTH_ISSUER"] == auth["vars"]["ISSUER"]
    assert mcp["services"][0]["service"] == "minhagrana-auth"  # service binding aponta para o auth do MESMO ambiente
    assert mcp["d1_databases"][0]["database_id"] == "d1-prod"
    assert auth["kv_namespaces"][0]["id"] == "kv-prod"
    assert mcp["queues"]["producers"][0]["queue"] == "minhagrana-jobs"


def test_staging_names_get_suffix():
    cfg = {**FULL, "D1_ID_STAGING": "d1-stg", "KV_ID_STAGING": "kv-stg"}
    out = rc.render(cfg, ["staging", "production"])
    stg = out["mcp"]["env"]["staging"]
    assert stg["name"] == "minhagrana-staging"
    assert stg["services"][0]["service"] == "minhagrana-auth-staging"
    assert stg["queues"]["consumers"][0]["dead_letter_queue"] == "minhagrana-jobs-staging-dlq"


def test_missing_and_validation():
    assert rc.missing_for(FULL, "staging") == ["D1_ID_STAGING", "KV_ID_STAGING"]
    assert rc.missing_for(FULL, "production") == []
    with pytest.raises(SystemExit):
        rc.validate({"LF_PREFIX": "Com Espaço"})
    with pytest.raises(SystemExit):
        rc.validate({"CF_WORKERS_SUBDOMAIN": "fulano.workers.dev"})


def test_env_file_and_process_env_precedence(tmp_path, monkeypatch):
    f = tmp_path / "x.env"
    f.write_text("LF_PREFIX=dofile\nGOOGLE_CLIENT_ID=abc # comentário\n# D1_ID_STAGING=nao\n", encoding="utf-8")
    monkeypatch.setenv("LF_PREFIX", "doambiente")
    cfg = rc.load_config(f)
    assert cfg["LF_PREFIX"] == "doambiente" and cfg["GOOGLE_CLIENT_ID"] == "abc" and "D1_ID_STAGING" not in cfg


def test_generated_file_is_valid_jsonc(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "ROOT", rc.ROOT)  # usa as bases reais
    out = rc.render(FULL, ["production"])
    text = rc.HEADER + json.dumps(out["mcp"])
    assert json.loads(rc.strip_jsonc(text))["env"]["production"]["name"] == "minhagrana"
