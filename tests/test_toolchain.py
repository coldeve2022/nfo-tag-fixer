# -*- coding: utf-8 -*-
"""外部工具定位：ffprobe 多路兜底 + 编译期编码器列表解析。

`ffmpeg -encoders` 的输出行**行首有空格**（` V....D h264_nvenc`），
用 `line[:1] in ("V","A","S")` 判断会全部漏掉，得到空集合 ——
于是"本机没有硬件编码器"的假象，而依赖它的功能静默降级。
这里用打桩输出把这条钉死。
"""

from __future__ import annotations

import sys

import pytest

from core import toolchain


@pytest.fixture(autouse=True)
def clear_cache():
    toolchain._CACHE.clear()
    yield
    toolchain._CACHE.clear()


def test_find_tool_prefers_configured_path(tmp_path):
    exe = tmp_path / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe.write_bytes(b"fake")
    assert toolchain.find_tool("ffprobe", str(exe)) == str(exe)


def test_find_tool_accepts_directory_as_configured(tmp_path):
    exe = tmp_path / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe.write_bytes(b"fake")
    assert toolchain.find_tool("ffprobe", str(tmp_path)) == str(exe)


def test_find_tool_finds_program_dir_bundled_copy(tmp_path):
    bundled = tmp_path / "ffmpeg" / "bin"
    bundled.mkdir(parents=True)
    exe = bundled / ("ffprobe.exe" if sys.platform == "win32" else "ffprobe")
    exe.write_bytes(b"fake")
    assert toolchain.find_tool("ffprobe", "", prog_dir=tmp_path) == str(exe)


def test_find_tool_returns_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(toolchain, "_common_roots", lambda: [])
    monkeypatch.setattr(toolchain.shutil, "which", lambda _n: None)
    assert toolchain.find_tool("definitely-not-a-real-tool", "",
                               prog_dir=tmp_path) == ""


ENCODER_OUTPUT = """\
Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC (codec h264)
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder (codec h264)
 V....D h264_qsv             H.264 / AVC (Intel Quick Sync Video)
 V....D h264_amf             AMD AMF H.264 Encoder (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
"""


def test_compiled_encoders_parses_leading_space_output(monkeypatch):
    monkeypatch.setattr(toolchain, "_run", lambda *a, **kw: (0, ENCODER_OUTPUT))
    found = toolchain.compiled_encoders("ffmpeg")
    assert {"libx264", "h264_nvenc", "h264_qsv", "h264_amf", "aac"} <= found
    assert "Encoders:" not in found
    assert "=" not in found


def test_compiled_encoders_is_cached(monkeypatch, tmp_path):
    tool = tmp_path / "ffmpeg"
    tool.write_bytes(b"x" * 10)
    calls = []

    def _fake_run(*a, **kw):
        calls.append(a)
        return 0, ENCODER_OUTPUT

    monkeypatch.setattr(toolchain, "_run", _fake_run)
    toolchain.compiled_encoders(str(tool))
    toolchain.compiled_encoders(str(tool))
    assert len(calls) == 1, "同一文件应命中缓存"


def test_cache_key_changes_with_file_mtime(tmp_path, monkeypatch):
    tool = tmp_path / "ffmpeg"
    tool.write_bytes(b"x")
    monkeypatch.setattr(toolchain, "_run", lambda *a, **kw: (0, ENCODER_OUTPUT))
    toolchain.compiled_encoders(str(tool))
    first = dict(toolchain._CACHE)
    tool.write_bytes(b"yyyy")            # 大小/mtime 变了
    toolchain.compiled_encoders(str(tool))
    assert len(toolchain._CACHE) > len(first), "换了文件必须让缓存失效"


def test_probe_encoder_falls_back_to_compiled_list_without_lavfi(monkeypatch):
    """极简构建没有 lavfi → 真跑必然失败，此时退回编译期列表，避免全部误判。"""
    def _run(_tool, args, timeout=10.0):
        if "-encoders" in args:
            return 0, ENCODER_OUTPUT
        return 1, "Unknown input format: 'lavfi'"

    monkeypatch.setattr(toolchain, "_run", _run)
    assert toolchain.probe_encoder("ffmpeg", "h264_nvenc") is True
    assert toolchain.probe_encoder("ffmpeg", "not_a_codec") is False


def test_probe_encoder_true_when_really_works(monkeypatch):
    monkeypatch.setattr(toolchain, "_run", lambda *a, **kw: (0, ""))
    assert toolchain.probe_encoder("ffmpeg", "libx264") is True


def test_probe_encoder_false_when_device_missing(monkeypatch):
    """编译期有 h264_qsv，但没有 Intel 显卡时真跑会报 MFX session 错误。"""
    def _run(_tool, args, timeout=10.0):
        if "-encoders" in args:
            return 0, ENCODER_OUTPUT
        return 1, "Error creating a MFX session: -9"

    monkeypatch.setattr(toolchain, "_run", _run)
    assert toolchain.probe_encoder("ffmpeg", "h264_qsv") is False


def test_doctor_reports_without_crashing():
    rows = toolchain.doctor()
    assert rows
    names = " ".join(n for n, _ in rows)
    assert "ffprobe" in names
    assert any("send2trash" in n for n, _ in rows)
    assert any("requests" in n for n, _ in rows)
