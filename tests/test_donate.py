"""donate 模块测试：付款码图片查找、系统预览打开、开关控制。

以及 core/platform.open_file 的三平台分支（macOS open / Windows startfile / Linux xdg-open）。
"""

from pathlib import Path

import pytest
from PIL import Image

from core import donate
from core import platform as core_platform


def _touch(path, data=b"x"):
    path.write_bytes(data)


# ===== _find_image =====

def test_find_image_jpg(tmp_path, monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    _touch(tmp_path / "wechat-pay.jpg")
    _touch(tmp_path / "alipay.jpg")
    # 关键字要避开会"误命中"其他文件的字串
    _touch(tmp_path / "shop-qr.png")

    assert donate._find_image("wechat").name == "wechat-pay.jpg"
    assert donate._find_image("alipay").name == "alipay.jpg"
    assert donate._find_image("missing") is None


def test_find_image_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    _touch(tmp_path / "WECHAT-pay.JPG")
    assert donate._find_image("wechat").name == "WECHAT-pay.JPG"


def test_find_image_missing_dir(monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", Path("/nonexistent/path/that/does/not/exist"))
    assert donate._find_image("wechat") is None
    assert donate._find_image("alipay") is None


def test_find_image_png(tmp_path, monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    _touch(tmp_path / "wechat.png")
    assert donate._find_image("wechat").name == "wechat.png"


def test_find_image_ignores_non_image(tmp_path, monkeypatch):
    """非 jpg/jpeg/png 后缀不应被当作付款码。"""
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    _touch(tmp_path / "wechat-notes.txt")
    assert donate._find_image("wechat") is None


# ===== platform.open_file（三平台分支） =====

def test_open_file_mac(monkeypatch):
    monkeypatch.setattr(core_platform, "IS_MAC", True)
    monkeypatch.setattr(core_platform, "IS_WIN", False)
    calls = []
    monkeypatch.setattr(core_platform.subprocess, "run",
                        lambda *a, **kw: calls.append(a[0]))
    core_platform.open_file("/tmp/x.jpg")
    assert calls == [["open", "/tmp/x.jpg"]]


def test_open_file_win(monkeypatch):
    monkeypatch.setattr(core_platform, "IS_MAC", False)
    monkeypatch.setattr(core_platform, "IS_WIN", True)
    calls = []
    monkeypatch.setattr(core_platform.os, "startfile",
                        lambda p: calls.append(p), raising=False)
    core_platform.open_file("/tmp/x.jpg")
    assert calls == ["/tmp/x.jpg"]


def test_open_file_linux(monkeypatch):
    monkeypatch.setattr(core_platform, "IS_MAC", False)
    monkeypatch.setattr(core_platform, "IS_WIN", False)
    calls = []
    monkeypatch.setattr(core_platform.subprocess, "run",
                        lambda *a, **kw: calls.append(a[0]))
    core_platform.open_file("/tmp/x.jpg")
    assert calls == [["xdg-open", "/tmp/x.jpg"]]


def test_open_file_swallows_errors(monkeypatch):
    """打开失败时不应把异常抛给调用方（只打印提示）。"""
    monkeypatch.setattr(core_platform, "IS_MAC", True)
    monkeypatch.setattr(core_platform, "IS_WIN", False)

    def boom(*a, **kw):
        raise OSError("no GUI")

    monkeypatch.setattr(core_platform.subprocess, "run", boom)
    core_platform.open_file("/tmp/x.jpg")  # 不应抛异常


# ===== print_donate_message =====

def test_donate_disabled_prints_nothing(capsys, tmp_path, monkeypatch):
    """SHOW_DONATE=false 时既不打印也不打开。"""
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    _touch(tmp_path / "wechat.jpg")
    _touch(tmp_path / "alipay.jpg")

    opened = []
    monkeypatch.setattr(donate, "_open_image", lambda p: opened.append(p) or True)
    monkeypatch.setattr(donate._cfg, "get_settings",
                        lambda: donate._cfg.Settings(show_donate=False))

    donate.print_donate_message()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert opened == []


def test_donate_enabled_prints_message_and_opens_both(capsys, tmp_path, monkeypatch):
    """SHOW_DONATE=true 时打印文案，并打开微信+支付宝两张付款码。"""
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    wechat = tmp_path / "wechat.jpg"
    alipay = tmp_path / "alipay.jpg"
    _touch(wechat)
    _touch(alipay)

    opened = []
    monkeypatch.setattr(donate, "_open_image", lambda p: opened.append(p) or True)
    monkeypatch.setattr(donate._cfg, "get_settings",
                        lambda: donate._cfg.Settings(show_donate=True))

    donate.print_donate_message()
    out = capsys.readouterr().out
    assert "如果喜欢这个工具" in out
    assert "你的支持对我很重要" in out
    assert opened == [wechat, alipay]


def test_donate_missing_images_no_crash(capsys, tmp_path, monkeypatch):
    """img/ 为空时不应抛异常，应打印友好提示且不尝试打开。"""
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)

    opened = []
    monkeypatch.setattr(donate, "_open_image", lambda p: opened.append(p) or True)
    monkeypatch.setattr(donate._cfg, "get_settings",
                        lambda: donate._cfg.Settings(show_donate=True))

    donate.print_donate_message()
    out = capsys.readouterr().out
    assert "如果喜欢这个工具" in out
    assert "未在" in out
    assert opened == []  # 找不到图就不应尝试打开


def test_donate_falls_back_to_path_when_open_fails(capsys, tmp_path, monkeypatch):
    """打开失败时降级为打印图片路径，让用户手动扫码。"""
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    wechat = tmp_path / "wechat.jpg"
    alipay = tmp_path / "alipay.jpg"
    _touch(wechat)
    _touch(alipay)

    monkeypatch.setattr(donate, "_open_image", lambda p: False)  # 模拟打开失败
    monkeypatch.setattr(donate._cfg, "get_settings",
                        lambda: donate._cfg.Settings(show_donate=True))

    donate.print_donate_message()
    out = capsys.readouterr().out
    assert "wechat.jpg" in out
    assert "alipay.jpg" in out
    assert "无法自动打开预览" in out


# ===== Settings 集成 =====

@pytest.fixture
def clean_settings(monkeypatch):
    """隔离真实 .env：from_env 会 load_dotenv 读到项目 .env，
    用户本机改过配置（如 SHOW_DONATE=false）会让「默认值」测试误判。"""
    monkeypatch.setattr(donate._cfg, "load_dotenv", None)
    monkeypatch.delenv("SHOW_DONATE", raising=False)
    donate._cfg._settings = None
    yield
    donate._cfg._settings = None


def test_settings_show_donate_default(clean_settings):
    s = donate._cfg.Settings.from_env()
    assert s.show_donate is True


def test_settings_show_donate_env_false(clean_settings, monkeypatch):
    monkeypatch.setenv("SHOW_DONATE", "false")
    s = donate._cfg.Settings.from_env()
    assert s.show_donate is False


# ===== GUI 右上角付款码（find_qr_images / load_thumbnail）=====

def test_find_qr_images_found(tmp_path, monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    (tmp_path / "wechat-pay.jpg").write_bytes(b"fake")
    (tmp_path / "alipay.jpg").write_bytes(b"fake")
    r = donate.find_qr_images()
    assert r["wechat"].name == "wechat-pay.jpg"
    assert r["alipay"].name == "alipay.jpg"


def test_find_qr_images_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(donate, "IMG_DIR", tmp_path)
    r = donate.find_qr_images()
    assert r["wechat"] is None
    assert r["alipay"] is None


def test_load_thumbnail_scales_to_square(tmp_path):
    p = tmp_path / "wechat-pay.jpg"
    Image.new("RGB", (500, 500), (255, 0, 0)).save(p, "JPEG")
    thumb = donate.load_thumbnail(p, size=32)
    assert thumb is not None
    assert thumb.size == (32, 32)


def test_load_thumbnail_invalid_returns_none(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not-an-image")
    assert donate.load_thumbnail(p, size=32) is None


def test_load_thumbnail_missing_file_returns_none(tmp_path):
    assert donate.load_thumbnail(tmp_path / "nope.jpg", size=32) is None
