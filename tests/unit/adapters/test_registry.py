import pytest

from core.adapters.registry import AdapterRegistry, load_sources_config


def test_load_sources_config(tmp_path):
    yml = tmp_path / "sources.yaml"
    yml.write_text("""
markets:
  ashare:
    enabled: true
    default_universe: ["000858.SZ"]
    index_symbols: ["000001.SH"]
""")
    cfg = load_sources_config(str(yml))
    assert cfg["markets"]["ashare"]["enabled"] is True
    assert cfg["markets"]["ashare"]["default_universe"] == ["000858.SZ"]


def test_registry_builds_adapters_for_enabled_markets():
    cfg = {"markets": {
        "ashare": {"enabled": True, "default_universe": [], "index_symbols": []},
        "hk":     {"enabled": True, "default_universe": [], "index_symbols": []},
        "us":     {"enabled": True, "default_universe": [], "index_symbols": []},
        "crypto": {"enabled": True, "default_universe": [], "index_symbols": []},
    }}
    reg = AdapterRegistry.from_config(cfg)
    assert set(reg.markets()) == {"ashare", "hk", "us", "crypto"}
    assert reg.get("ashare").market == "ashare"


def test_registry_skips_disabled():
    cfg = {"markets": {
        "ashare": {"enabled": True, "default_universe": [], "index_symbols": []},
        "us":     {"enabled": False, "default_universe": [], "index_symbols": []},
    }}
    reg = AdapterRegistry.from_config(cfg)
    assert "us" not in reg.markets()


def test_registry_get_unknown_raises():
    reg = AdapterRegistry.from_config({"markets": {}})
    with pytest.raises(KeyError):
        reg.get("ashare")
