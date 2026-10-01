"""Unit tests must not start collectors from the developer's local .env."""
import os


# Tests that exercise these modes enable them explicitly with monkeypatch.
os.environ['MLB_HIGHLIGHTS_ENABLED'] = 'false'
os.environ['REDDIT_RSS_ENABLED'] = 'false'
os.environ['SOCIAL_CLIP_GATE'] = 'false'
