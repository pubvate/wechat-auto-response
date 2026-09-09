"""config 模块测试：环境变量 Settings 与人设白名单 personas.json 的加载/映射。"""

import json

import pytest

from core import config


@pytest.fixture
def reset(monkeypatch, tmp_path):
    """重置模块级缓存，并把 personas.json 指向临时文件。"""
    config._config_cache = {"mtime": None, "data": None}
    config._config_warned.clear()
    config._settings = None
    path = tmp_path / "personas.json"
    monkeypatch.setattr(config, "CONFIG_PATH", str(path))
    return path


def write_personas(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_default_persona_fallback(reset):
    write_personas(reset, {"personas": {"warm": "暖心人设"}, "contacts": {}})
    assert config.get_persona_key("任何人") == "warm"
    assert config.get_system_prompt("任何人") == "暖心人设"


def test_contact_persona_mapping(reset):
    write_personas(reset, {
        "default_persona": "d",
        "personas": {"d": "默认", "work": "工作", "humor": "幽默"},
        "contacts": {"小明": "work", "同事小李": "humor"},
    })
    assert config.get_persona_key("小明") == "work"
    assert config.get_persona_key("同事小李") == "humor"
    assert config.get_system_prompt("小明") == "工作"
    assert config.get_system_prompt("同事小李") == "幽默"


def test_null_contact_uses_default(reset):
    write_personas(reset, {
        "default_persona": "d",
        "personas": {"d": "默认", "x": "X"},
        "contacts": {"老王": None},
    })
    assert config.get_persona_key("老王") == "d"


def test_enabled_false_excluded_from_whitelist(reset):
    write_personas(reset, {
        "personas": {"d": "默认"},
        "contacts": {"小明": "d", "小红": {"persona": "d", "enabled": False}},
    })
    assert config.get_whitelist() == ["小明"]


def test_invalid_persona_key_fallback(reset):
    write_personas(reset, {
        "default_persona": "d",
        "personas": {"d": "默认"},
        "contacts": {"小明": "不存在的key"},
    })
    assert config.get_persona_key("小明") == "d"


def test_missing_file_falls_back(reset):
    # 文件不存在：白名单为空、用兜底人设
    assert config.get_whitelist() == []
    assert config.get_system_prompt("任何人") == config.FALLBACK_SYSTEM_PROMPT


def test_broken_json_falls_back(reset):
    reset.write_text("{ 坏掉的 json", encoding="utf-8")
    config._config_cache = {"mtime": None, "data": None}
    assert config.get_whitelist() == []
    assert config.get_system_prompt("任何人") == config.FALLBACK_SYSTEM_PROMPT


def test_persona_shorthand(reset):
    write_personas(reset, {"personas": {"简洁": "简洁人设"}, "contacts": {"A": "简洁"}})
    assert config.get_system_prompt("A") == "简洁人设"


def test_hot_reload_on_mtime(reset, monkeypatch):
    write_personas(reset, {"personas": {"d": "第一版"}, "contacts": {"A": "d"}})
    assert config.get_system_prompt("A") == "第一版"
    # 改文件内容后应自动生效（mtime 变化）
    import time
    time.sleep(0.01)
    write_personas(reset, {"personas": {"d": "第二版"}, "contacts": {"A": "d"}})
    assert config.get_system_prompt("A") == "第二版"


def test_settings_defaults(monkeypatch):
    config._settings = None
    s = config.Settings()
    assert s.model == "deepseek-chat"
    assert s.dry_run is False
    assert s.memory_analyze_interval_hours == 6
    assert s.context_base_count == 4


def test_settings_env_override(monkeypatch):
    config._settings = None
    monkeypatch.setenv("OPENAI_MODEL", "custom-model")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("MEMORY_ANALYZE_INTERVAL_HOURS", "12")
    monkeypatch.setenv("CONTEXT_MAX_COUNT", "32")
    s = config.Settings.from_env()
    assert s.model == "custom-model"
    assert s.dry_run is True
    assert s.memory_analyze_interval_hours == 12
    assert s.context_max_count == 32


def test_env_bool_and_int_helpers():
    assert config._env_bool("NOPE", True) is True
    assert config._env_int("NOPE", 7) == 7
