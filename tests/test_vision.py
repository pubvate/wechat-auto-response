"""vision 模块测试：缓存命中、未配置降级、API 调用与结果解析。"""

import json
import types

import pytest
from PIL import Image

from core import vision


@pytest.fixture
def vision_env(monkeypatch, tmp_path):
    """缓存文件指到临时目录。"""
    monkeypatch.setattr(vision, "CACHE_PATH", str(tmp_path / "cache.json"))
    return tmp_path


def _make_settings(**kw):
    base = {"enable_stickers": True, "vision_api_key": "k", "vision_base_url": "u",
            "vision_model": "m"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _img(color=(200, 100, 50)):
    return Image.new("RGB", (120, 120), color)


def test_analyze_returns_none_for_none_img(vision_env):
    assert vision.analyze_sticker(None) == (None, None)


def test_analyze_not_configured_degrades(monkeypatch, vision_env):
    monkeypatch.setattr(vision.config, "_settings",
                        _make_settings(vision_api_key="", vision_base_url="",
                                       vision_model=""))
    desc, emotion = vision.analyze_sticker(_img())
    assert desc is None and emotion is None
    # 未配置不写缓存（下回配置好还能重新分析）
    assert not (vision_env / "cache.json").exists()


def test_analyze_cache_hit_no_api_call(monkeypatch, vision_env):
    key = vision.hashlib.md5(vision._image_bytes(_img())).hexdigest()
    (vision_env / "cache.json").write_text(
        json.dumps({key: {"desc": "开心大笑", "emotion": "开心"}}), encoding="utf-8")

    def forbidden_client():
        raise AssertionError("命中缓存不应调用 API")
    monkeypatch.setattr(vision.config, "_settings", _make_settings())
    monkeypatch.setattr(vision, "_vision_ready", forbidden_client)

    desc, emotion = vision.analyze_sticker(_img())
    assert (desc, emotion) == ("开心大笑", "开心")


def test_analyze_calls_api_and_parses(monkeypatch, vision_env):
    monkeypatch.setattr(vision.config, "_settings", _make_settings())

    class FakeMsg:
        content = "情绪：开心\n含义：大笑的表情包"
    class FakeChoice:
        message = FakeMsg()
    class FakeRespOk:
        choices = [FakeChoice()]

    class FakeOpenAI:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **kw2: FakeRespOk()))

    import sys
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    desc, emotion = vision.analyze_sticker(_img())
    assert emotion == "开心"
    assert desc == "大笑的表情包"
    # 已写入缓存
    cache = json.loads((vision_env / "cache.json").read_text(encoding="utf-8"))
    assert len(cache) == 1


def test_analyze_api_error_degrades(monkeypatch, vision_env):
    monkeypatch.setattr(vision.config, "_settings", _make_settings())

    class FakeOpenAI:
        def __init__(self, **kw):
            raise RuntimeError("network down")

    import sys
    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    desc, emotion = vision.analyze_sticker(_img())
    assert desc is None and emotion is None
