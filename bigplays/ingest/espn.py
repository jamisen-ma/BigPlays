from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Generator, Iterable, List, Optional

import requests

from bigplays.models import League, ScoringEvent
from bigplays.utils.time_utils import parse_espn_timestamp


NBA_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
NFL_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
MLB_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
# ESPN falls back to 25 events for an oversized limit; 100 returns the full FBS slate.
NCAAF_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard?groups=80&limit=100"
SCOREBOARDS = {League.NBA: NBA_SCOREBOARD, League.NFL: NFL_SCOREBOARD, League.NCAAF: NCAAF_SCOREBOARD,
              League.MLB: MLB_SCOREBOARD}


@dataclass
class GameState:
    game_id: str
    league: League
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    status: str  # pre, in, post
    last_update: Optional[datetime]
    inning: Optional[int] = None
    inning_half: Optional[str] = None
    balls: Optional[int] = None
    strikes: Optional[int] = None
    outs: Optional[int] = None
    status_detail: str = ''


def fetch_scoreboard(league: League) -> dict:
    url = SCOREBOARDS[league]
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def parse_games(league: League, data: dict) -> List[GameState]:
    events = data.get("events", [])
    results: List[GameState] = []
    for ev in events:
        competitions = ev.get("competitions", [])
        if not competitions:
            continue
        comp = competitions[0]
        competitors = comp.get("competitors", [])
        if len(competitors) != 2:
            continue
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        status = ev.get("status", {}).get("type", {}).get("state", "pre").lower()
        details = ev.get('status', {})
        situation = comp.get('situation', {})
        status_detail = details.get('type', {}).get('shortDetail', '')
        half = next((half for half in ('Top', 'Bottom') if status_detail.lower().startswith(half.lower())), None)
        game_id = ev.get("id")
        results.append(
            GameState(
                game_id=game_id,
                league=league,
                home_team=home.get("team", {}).get("abbreviation", "HOME"),
                away_team=away.get("team", {}).get("abbreviation", "AWAY"),
                home_score=int(home.get("score", 0)),
                away_score=int(away.get("score", 0)),
                status=status,
                last_update=parse_espn_timestamp(ev.get("date")) if ev.get("date") else None,
                inning=details.get('period') if league == League.MLB and status != 'pre' else None,
                inning_half=half if league == League.MLB else None,
                balls=situation.get('balls') if league == League.MLB else None,
                strikes=situation.get('strikes') if league == League.MLB else None,
                outs=situation.get('outs') if league == League.MLB else None,
                status_detail=status_detail,
            )
        )
    return results


def detect_scoring_events(prev: GameState, curr: GameState, occurred_utc: datetime) -> Iterable[ScoringEvent]:
    if prev.home_score == curr.home_score and prev.away_score == curr.away_score:
        return []
    desc = f"{curr.away_team} {curr.away_score} - {curr.home_team} {curr.home_score}"
    # ESPN scoreboard payload does not give play-by-play here; this is a coarse scoring change event.
    return [
        ScoringEvent(
            game_id=curr.game_id,
            league=curr.league,
            occurred_utc=occurred_utc,
            description=desc,
            home_score=curr.home_score,
            away_score=curr.away_score,
            period="",
            clock="",
        )
    ]


def poll_scoreboards(leagues: List[League]) -> Generator[List[GameState], None, None]:
    payloads = {league: fetch_scoreboard(league) for league in leagues}
    games = {league: parse_games(league, payloads[league]) for league in leagues}
    yield [g for lst in games.values() for g in lst]
