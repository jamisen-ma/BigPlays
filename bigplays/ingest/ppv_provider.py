"""Verified PPV endpoints and strict event-path validation shared by the gateway."""
import re

API_BASE = 'https://api.ppv.st/api'
PUBLIC_ORIGIN = 'https://ppv.st'
EVENT_PATH = r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*'


def valid_event_uri(uri):
    return isinstance(uri, str) and len(uri) <= 400 and re.fullmatch(EVENT_PATH, uri) is not None


def valid_event_url(url):
    # Retain old event links as aliases; all metadata comes from the verified API.
    return isinstance(url, str) and len(url) <= 512 and re.fullmatch(
        rf'https://ppv\.(?:to|st)/live/{EVENT_PATH}/?', url) is not None
