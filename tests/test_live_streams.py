from bigplays.ingest.live_streams import match_games, catalog_entries


def scoreboard(state='in'):
    return {'nba': {'events': [{'id': '1', 'name': 'Boston Celtics at Los Angeles Lakers',
        'status': {'type': {'state': state}}, 'competitions': [{'competitors': [
            {'team': {'displayName': 'Boston Celtics', 'name': 'Celtics', 'abbreviation': 'BOS'}},
            {'team': {'displayName': 'Los Angeles Lakers', 'name': 'Lakers', 'abbreviation': 'LAL'}}]}]}]}}


def test_matches_both_teams_only():
    entries = [{'name': 'Celtics vs Lakers', 'uri_name': 'nba-celtics-lakers'},
               {'name': 'Lakers vs Bulls', 'uri_name': 'nba-bulls-lakers'}]
    games = match_games(scoreboard(), entries)
    assert games[0]['url'] == 'https://ppv.to/live/nba-celtics-lakers'
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
