from pathlib import Path

import pytest

from bigplays.media import hls_clipper


def test_recent_clip_uses_media_duration_despite_bursty_arrival_times(monkeypatch, tmp_path):
    segments = [Path(f'20260926-1800{second:02}.ts') for second in (0, 1, 12, 13, 27, 28)]
    monkeypatch.setattr(hls_clipper, 'media_duration_seconds', lambda _: 4.0)
    concatenated = []
    monkeypatch.setattr(hls_clipper, 'concat_segments_to_mp4', lambda files, _: concatenated.extend(files))
    out = tmp_path / 'clip.mp4'
    hls_clipper.clip_recent(segments, 14, out, {'league': 'ncaaf'})
    assert concatenated == segments[-4:]
    assert out.with_suffix('.json').exists()


def test_recent_clip_waits_for_enough_footage(monkeypatch, tmp_path):
    monkeypatch.setattr(hls_clipper, 'media_duration_seconds', lambda _: 2.0)
    with pytest.raises(ValueError, match='still filling'):
        hls_clipper.clip_recent([Path('one.ts'), Path('two.ts')], 14, tmp_path / 'clip.mp4', {})
    assert not list(tmp_path.iterdir())
