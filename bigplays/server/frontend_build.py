"""Keep frontend/dist in sync with frontend/src so `server run` never serves a stale build.

The server serves whatever is in frontend/dist. A clone that was built once and then
pulled would otherwise keep showing the old UI until someone remembers to rebuild.
Git sets a changed file's mtime to checkout time, so "any source newer than
dist/index.html" reliably detects that case. Set FRONTEND_AUTO_BUILD=false to opt out.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parents[2] / 'frontend'
SOURCES = ('src', 'public', 'index.html', 'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json')


def newest_source_mtime(root: Path = FRONTEND) -> float:
    newest = 0.0
    for name in SOURCES:
        path = root / name
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
        elif path.is_dir():
            newest = max([newest, *(f.stat().st_mtime for f in path.rglob('*') if f.is_file())])
    return newest


def build_is_stale(root: Path = FRONTEND) -> bool:
    index = root / 'dist' / 'index.html'
    return not index.is_file() or index.stat().st_mtime < newest_source_mtime(root)


def ensure_frontend_built(root: Path = FRONTEND, run=subprocess.run) -> str:
    """Return 'current', 'built', 'skipped' (opted out) or 'stale' (could not rebuild)."""
    if not build_is_stale(root):
        return 'current'
    if os.environ.get('FRONTEND_AUTO_BUILD', 'true').lower() in ('0', 'false', 'no', 'off'):
        log.warning('frontend/dist is out of date and FRONTEND_AUTO_BUILD is off; serving the old build')
        return 'skipped'
    npm = shutil.which('npm')
    if npm is None:
        log.error('frontend/dist is out of date and npm is not installed; serving the OLD UI. '
                  'Install Node, then run: npm install --prefix frontend && npm run build --prefix frontend')
        return 'stale'
    try:
        if not (root / 'node_modules').is_dir():
            log.info('installing frontend dependencies…')
            run([npm, 'ci' if (root / 'package-lock.json').is_file() else 'install', '--no-audit', '--no-fund'],
                cwd=root, check=True)
        log.info('frontend source changed since the last build; rebuilding frontend/dist…')
        run([npm, 'run', 'build'], cwd=root, check=True)
    except (subprocess.CalledProcessError, OSError) as error:
        log.error('frontend build failed (%s); serving the OLD UI. Run: npm run build --prefix frontend', error)
        return 'stale'
    return 'built'
