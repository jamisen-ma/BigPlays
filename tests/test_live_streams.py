import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from bigplays.ingest.live_streams import match_games, catalog_entries, discover


def scoreboard(state='in'):
    return {'nba': {'events': [{'id': '1', 'name': 'Boston Celtics at Los Angeles Lakers',
        'status': {'type': {'state': state}}, 'competitions': [{'competitors': [
            {'team': {'displayName': 'Boston Celtics', 'name': 'Celtics', 'abbreviation': 'BOS'}},
            {'team': {'displayName': 'Los Angeles Lakers', 'name': 'Lakers', 'abbreviation': 'LAL'}}]}]}]}}


def nfl_scoreboard():
    return {'events': [{'id': '2', 'name': 'New York Jets at Buffalo Bills',
        'status': {'type': {'state': 'in'}}, 'competitions': [{'competitors': [
            {'team': {'displayName': 'New York Jets', 'name': 'Jets', 'abbreviation': 'NYJ'}},
            {'team': {'displayName': 'Buffalo Bills', 'name': 'Bills', 'abbreviation': 'BUF'}}]}]}]}


def test_matches_both_teams_only():
    entries = [{'name': 'Celtics vs Lakers', 'uri_name': 'nba-celtics-lakers'},
               {'name': 'Lakers vs Bulls', 'uri_name': 'nba-bulls-lakers'}]
    games = match_games(scoreboard(), entries)
    assert games[0]['url'] == 'https://ppv.st/live/nba-celtics-lakers'
    assert match_games(scoreboard('post'), entries) == []


def test_ambiguous_missing_and_untrusted_slugs():
    entries = [{'name': 'Celtics vs Lakers', 'uri_name': s} for s in ['one', 'two', '//evil.test']]
    game = match_games(scoreboard(), entries)[0]
    assert game['status'] == 'ambiguous' and game['url'] is None
    assert len(game['candidates']) == 2
    assert match_games(scoreboard(), [])[0]['status'] == 'unmatched'


def test_catalog_shapes():
    entry = {'name': 'Test', 'uri_name': 'test'}
    assert catalog_entries({'streams': [{'streams': [entry]}]}) == [entry]
    assert catalog_entries({'success': True, 'data': [entry]}) == [entry]


def mock_sources(monkeypatch, nba=None, nfl=None, ncaaf=None, mlb=None, catalog=None, failed=()):
    def respond(request):
        source = 'ppv' if request.url.host == 'api.ppv.st' else request.url.path.split('/')[-2]
        if source == 'college-football':
            source = 'ncaaf'
            assert request.url.params['groups'] == '80'
            assert request.url.params['limit'] == '100'
        if source in failed:
            raise httpx.ConnectError('source unavailable', request=request)
        payload = {'nba': nba if nba is not None else {'events': []},
                   'nfl': nfl if nfl is not None else {'events': []},
                   'ncaaf': ncaaf if ncaaf is not None else {'events': []},
                   'mlb': mlb if mlb is not None else {'events': []},
                   'ppv': catalog if catalog is not None else {'data': []}}[source]
        return httpx.Response(200, json=payload)
    original = httpx.AsyncClient
    monkeypatch.setattr('bigplays.ingest.live_streams.httpx.AsyncClient',
                        lambda **kw: original(transport=httpx.MockTransport(respond), **kw))


def test_catalog_failure_preserves_live_games(monkeypatch):
    mock_sources(monkeypatch, nba=scoreboard()['nba'], failed=('ppv',))
    result = asyncio.run(discover())
    assert result['ok'] and result['scoreboards_ok']
    assert result['games'][0]['name'] == 'Boston Celtics at Los Angeles Lakers'
    assert result['games'][0]['status'] == 'catalog_unavailable'
    assert result['games'][0]['url'] is None
    assert [warning['stage'] for warning in result['warnings']] == ['ppv']


def test_one_scoreboard_failure_preserves_matching_for_other_league(monkeypatch):
    mock_sources(monkeypatch, nfl=nfl_scoreboard(), failed=('nba',),
                 catalog={'data': [{'name': 'Jets vs Bills', 'uri_name': 'test'}]})
    result = asyncio.run(discover())
    assert result['ok'] and not result['scoreboards_ok']
    assert result['games'][0]['league'] == 'nfl'
    assert result['games'][0]['url'] == 'https://ppv.st/live/test'
    assert [warning['stage'] for warning in result['warnings']] == ['nba']


def test_no_live_games_still_shows_upcoming_when_catalog_fails(monkeypatch):
    nba = scoreboard('pre')['nba']
    nba['events'][0]['date'] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    mock_sources(monkeypatch, nba=nba, failed=('ppv',))
    result = asyncio.run(discover())
    assert result['ok'] and result['scoreboards_ok']
    assert result['games'] == []
    assert result['upcoming'][0]['game_id'] == '1'
    assert result['upcoming'][0]['league'] == 'nba'


def test_total_scoreboard_failure_is_not_reported_as_no_live_games(monkeypatch):
    mock_sources(monkeypatch, failed=('nba', 'nfl', 'ncaaf', 'mlb'))
    result = asyncio.run(discover())
    assert not result['ok'] and not result['scoreboards_ok']
    assert result['stage'] == 'discovery'
    assert len(result['warnings']) == 4


def test_mlb_match_rejects_same_teams_tagged_as_another_league(monkeypatch):
    board = {'events': [{'id': '401907896', 'name': 'Chicago White Sox at Houston Astros',
                        'status': {'type': {'state': 'in'}},
                        'competitions': [{'competitors': [
                            {'team': {'displayName': 'Chicago White Sox', 'name': 'White Sox', 'abbreviation': 'CWS'}},
                            {'team': {'displayName': 'Houston Astros', 'name': 'Astros', 'abbreviation': 'HOU'}},
                        ]}]}]}
    wrong = {'name': 'White Sox vs Astros', 'uri_name': 'nfl/wrong', 'tag': 'NFL'}
    correct = {'name': 'White Sox vs Astros', 'uri_name': 'mlb/white-sox-astros', 'tag': 'MLB'}
    mock_sources(monkeypatch, mlb=board, catalog={'data': [wrong, correct]})
    result = asyncio.run(discover())
    assert result['scoreboards_ok']
    game, = result['games']
    assert game['league'] == 'mlb' and game['game_id'] == '401907896'
    assert game['status'] == 'matched'
    assert game['url'] == 'https://ppv.st/live/mlb/white-sox-astros'
    assert match_games({'mlb': board}, [wrong])[0]['status'] == 'unmatched'


@pytest.mark.parametrize('payload', [[], {'events': [42]}, {'events': [{'status': None}]}])
def test_invalid_scoreboard_does_not_hide_other_league(monkeypatch, payload):
    mock_sources(monkeypatch, nba=payload, nfl=nfl_scoreboard())
    result = asyncio.run(discover())
    assert result['ok'] and not result['scoreboards_ok']
    assert result['games'][0]['league'] == 'nfl'
    assert result['warnings'][0]['stage'] == 'nba'


def college_scoreboard(state='in'):
    return {'events': [{'id': '3', 'name': 'Georgia Bulldogs at Mississippi State Bulldogs',
        'date': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        'status': {'type': {'state': state}}, 'competitions': [{'competitors': [
            {'team': {'displayName': 'Georgia Bulldogs', 'shortDisplayName': 'Georgia',
                      'location': 'Georgia', 'name': 'Bulldogs', 'abbreviation': 'UGA'}},
            {'team': {'displayName': 'Mississippi State Bulldogs', 'shortDisplayName': 'Mississippi St',
                      'location': 'Mississippi State', 'name': 'Bulldogs', 'abbreviation': 'MSST'}}]}]}]}


def test_college_match_uses_school_names_not_shared_mascots(monkeypatch):
    mock_sources(monkeypatch, ncaaf=college_scoreboard(), catalog={'data': [
        {'name': 'Georgia vs Mississippi State', 'uri_name': 'georgia-mississippi-state'},
        {'name': 'Fresno State Bulldogs vs Louisiana Tech Bulldogs', 'uri_name': 'other-bulldogs'}]})
    result = asyncio.run(discover())
    assert result['scoreboards_ok']
    game = result['games'][0]
    assert game['league'] == 'ncaaf'
    assert game['game_id'] == '3'
    assert game['status'] == 'matched'
    assert game['url'] == 'https://ppv.st/live/georgia-mississippi-state'


def test_college_schedule_survives_provider_outage(monkeypatch):
    mock_sources(monkeypatch, ncaaf=college_scoreboard('pre'), failed=('ppv',))
    result = asyncio.run(discover())
    assert result['ok'] and result['scoreboards_ok']
    assert result['upcoming'][0]['league'] == 'ncaaf'
    assert result['upcoming'][0]['game_id'] == '3'


def test_college_shared_mascots_do_not_create_match():
    games = match_games({'ncaaf': college_scoreboard()}, [
        {'name': 'Bulldogs vs Bulldogs', 'uri_name': 'bulldogs-bulldogs'}])
    assert games[0]['status'] == 'unmatched'


def test_upcoming_college_matches_dated_listing_but_rejects_wrong_league(monkeypatch):
    board = college_scoreboard('pre')
    start = datetime.fromisoformat(board['events'][0]['date']).timestamp()
    mock_sources(monkeypatch, ncaaf=board, catalog={'streams': [{'category': 'American Football', 'streams': [
        {'name': 'Georgia vs Mississippi State', 'uri_name': 'ncaaf/2026-09-26/uga-msst', 'tag': 'CFB',
         'starts_at': start, 'ends_at': start + 14400},
        {'name': 'Georgia vs Mississippi State', 'uri_name': 'nfl/wrong', 'tag': 'NFL'},
        {'name': 'Georgia vs Mississippi State', 'uri_name': 'ncaaf/wrong-date', 'starts_at': start + 86400},
    ]}]})
    result = asyncio.run(discover())
    assert result['games'] == []
    assert result['upcoming'][0]['url'] == 'https://ppv.st/live/ncaaf/2026-09-26/uga-msst'
    assert result['upcoming'][0]['status'] == 'matched'
