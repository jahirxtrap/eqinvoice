from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUTHOR = "jahirtrap"
REPOSITORY = "https://github.com/jahirxtrap/eqinvoice"
PLUGIN = ROOT / "plugin"
SERVER_ICON = ROOT / "src" / "eqinvoice_mcp" / "icon.svg"
SETTINGS = ("EQINVOICE_BOOK", "EQINVOICE_P12", "EQINVOICE_P12_PASSWORD")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
MARKETPLACE = _json(ROOT / ".claude-plugin" / "marketplace.json")
MANIFEST = _json(PLUGIN / ".claude-plugin" / "plugin.json")
LAUNCHED = _json(PLUGIN / ".mcp.json")["mcpServers"]


def test_the_marketplace_lists_the_plugin_once():
    assert MARKETPLACE["name"] == PROJECT["name"]
    assert MARKETPLACE["owner"]["name"] == AUTHOR
    assert [plugin["name"] for plugin in MARKETPLACE["plugins"]] == [PROJECT["name"]]


def test_the_manifest_matches_its_entry_and_the_package():
    entry = MARKETPLACE["plugins"][0]
    assert MANIFEST["name"] == entry["name"] == PROJECT["name"]
    assert MANIFEST["version"] == entry["version"] == PROJECT["version"]
    assert MANIFEST["description"] == entry["description"]
    assert MANIFEST["keywords"] == entry["tags"]
    assert MANIFEST["author"]["name"] == AUTHOR
    assert MANIFEST["repository"] == MANIFEST["homepage"] == REPOSITORY
    assert entry["source"] == "./plugin"


def test_the_plugin_shows_the_servers_icon():
    assert MANIFEST["icon"] == "./assets/icon.svg"
    assert (PLUGIN / MANIFEST["icon"]).read_bytes() == SERVER_ICON.read_bytes()


def test_the_plugin_launches_the_published_server_with_its_settings():
    script = next(iter(PROJECT["scripts"]))
    server = LAUNCHED[PROJECT["name"]]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", f"{PROJECT['name']}[mcp]=={PROJECT['version']}", script]
    assert sorted(server["env"]) == sorted(SETTINGS)
    asked = {f"${{user_config.{key}}}" for key in MANIFEST["userConfig"]}
    assert set(server["env"].values()) == asked
