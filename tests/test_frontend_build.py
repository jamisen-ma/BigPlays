import os

from bigplays.server import frontend_build as fb


def make_frontend(tmp_path, built_at=None, src_at=1000.0, node_modules=True):
    (tmp_path / 'src').mkdir()
    src = tmp_path / 'src' / 'App.tsx'
    src.write_text('x')
    os.utime(src, (src_at, src_at))
    (tmp_path / 'package-lock.json').write_text('{}')
    os.utime(tmp_path / 'package-lock.json', (1.0, 1.0))
    if node_modules:
        (tmp_path / 'node_modules').mkdir()
    if built_at is not None:
        (tmp_path / 'dist').mkdir()
        index = tmp_path / 'dist' / 'index.html'
        index.write_text('<html>')
        os.utime(index, (built_at, built_at))
    return tmp_path


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, cwd, check):
        self.calls.append(cmd[1:])


def test_current_build_is_left_alone(tmp_path, monkeypatch):
    root = make_frontend(tmp_path, built_at=2000.0)
    run = Recorder()
    assert fb.ensure_frontend_built(root, run) == 'current' and run.calls == []


def test_source_newer_than_dist_triggers_rebuild(tmp_path, monkeypatch):
    monkeypatch.setattr(fb.shutil, 'which', lambda name: '/usr/bin/npm')
    root = make_frontend(tmp_path, built_at=500.0)
    run = Recorder()
    assert fb.ensure_frontend_built(root, run) == 'built'
    assert run.calls == [['run', 'build']]


def test_missing_dist_and_node_modules_installs_then_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(fb.shutil, 'which', lambda name: '/usr/bin/npm')
    root = make_frontend(tmp_path, built_at=None, node_modules=False)
    run = Recorder()
    assert fb.ensure_frontend_built(root, run) == 'built'
    assert run.calls == [['ci', '--no-audit', '--no-fund'], ['run', 'build']]


def test_no_npm_reports_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(fb.shutil, 'which', lambda name: None)
    root = make_frontend(tmp_path, built_at=500.0)
    assert fb.ensure_frontend_built(root, Recorder()) == 'stale'


def test_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv('FRONTEND_AUTO_BUILD', 'false')
    root = make_frontend(tmp_path, built_at=500.0)
    run = Recorder()
    assert fb.ensure_frontend_built(root, run) == 'skipped' and run.calls == []


def test_failed_build_reports_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(fb.shutil, 'which', lambda name: '/usr/bin/npm')
    root = make_frontend(tmp_path, built_at=500.0)

    def failing(cmd, cwd, check):
        raise fb.subprocess.CalledProcessError(1, cmd)
    assert fb.ensure_frontend_built(root, failing) == 'stale'
