from __future__ import annotations

"""Scripted "viral" plays used by the demo simulator and the synthetic clip generator.

These are real moments from the most recent seasons (2025-26 NBA, 2025 NFL). Each carries the official YouTube clip
(played through YouTube's embed player; nothing is downloaded) plus the fields
the real pipeline produces: the scoring/game event, the heuristic reasons that
fired, a Claude-style judgment (hype score, tags, title, rationale), and the
commentary/social context that was retrieved for the LLM. Scores and clocks are
from the actual games; commentary lines are paraphrased, not verbatim calls.
A synthetic rendered clip is generated for every play as an offline fallback.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DemoPlay:
    play_id: str
    league: str  # nba | nfl
    game_id: str
    away: str
    home: str
    away_color: str
    home_color: str
    away_before: int
    home_before: int
    away_after: int
    home_after: int
    period: str
    clock: str
    kind: str  # three | dunk | block | buzzer | pick_six | sack | deep_pass  (synthetic fallback scene)
    player: str
    team: str  # abbreviation of the team that made the play
    description: str  # play-by-play line
    title: str  # LLM-generated headline
    rationale: str  # LLM rationale
    hype_score: float
    base_score: float
    reasons: List[str]
    tags: List[str]
    commentary: List[str] = field(default_factory=list)  # RAG: retrieved commentary snippets (paraphrased)
    social: List[str] = field(default_factory=list)  # social burst samples
    social_score: float = 0.0
    youtube_id: Optional[str] = None
    youtube_start: int = 0
    youtube_end: Optional[int] = None
    source_title: str = ""
    source_channel: str = ""
    date: str = ""
    occurred_utc: Optional[str] = None
    play_by_play_url: Optional[str] = None
    season: Optional[int] = None
    week: Optional[int] = None
    source_play_id: Optional[str] = None
    source_url: Optional[str] = None
    video_url: Optional[str] = None
    video_start: float = 0
    video_end: Optional[float] = None
    media_kind: str = 'animation'


PLAYS: List[DemoPlay] = [
    # ------------------------------------------------------------------ NBA 2025-26
    DemoPlay(
        play_id="nba_sas_nyk_2026_finals_g4", league="nba", game_id="2026-06-10-SAS-NYK", date="2026-06-10",
        away="SAS", home="NYK", away_color="#C4CED4", home_color="#F58426",
        away_before=106, home_before=105, away_after=106, home_after=107,
        period="Q4", clock="0:01.2", kind="buzzer", player="OG Anunoby", team="NYK",
        description="Jalen Brunson misses 26-foot three point jumper. OG Anunoby right-handed tip-in with 1.2 seconds left. Knicks complete a 29-point comeback in Game 4 of the NBA Finals.",
        title="ANUNOBY TIP-IN CAPS THE BIGGEST COMEBACK IN FINALS HISTORY",
        rationale="Game-winning putback with 1.2 seconds left to finish a 29-point Finals comeback at Madison Square Garden. Lead change, clutch window and social burst all maxed; the highest-leverage play of the season.",
        hype_score=0.99, base_score=0.85,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["NBA Finals", "game-winner", "tip-in", "29-point comeback", "Anunoby"],
        commentary=["Brunson for the lead... off the rim... Anunoby! Tip-in! Knicks lead with 1.2 to go!", "Down 29 at the half. The Garden is shaking."],
        social=["OG ANUNOBY JUST WON GAME 4 OF THE FINALS ON A TIP-IN", "29 POINTS DOWN AT HALFTIME. 29.", "MSG is going to fall into the East River"],
        social_score=0.99,
        youtube_id="sOQKRjP3ZJk", youtube_start=2, youtube_end=12, source_title="OG Anunoby's INSANE TIP-IN Game 4 vs Spurs | June 10, 2026", source_channel="NBA",
    ),
    DemoPlay(
        play_id="nba_nyk_sas_2026_finals_g5", league="nba", game_id="2026-06-13-NYK-SAS", date="2026-06-13",
        away="NYK", home="SAS", away_color="#F58426", home_color="#C4CED4",
        away_before=88, home_before=88, away_after=90, home_after=88,
        period="Q4", clock="1:05", kind="three", player="Jalen Brunson", team="NYK",
        description="Jalen Brunson makes 12-foot floating jumper over Stephon Castle. Knicks lead for good in the title clincher; Brunson finishes with a Finals-record 45 points.",
        title="BRUNSON'S FLOATER PUTS THE KNICKS 65 SECONDS FROM A TITLE",
        rationale="Go-ahead bucket in a tied championship clincher, capping 13 straight Knicks points from the Finals MVP. Clutch window plus lead change plus a 53-year title drought ending.",
        hype_score=0.97, base_score=0.80,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["NBA Finals", "clincher", "Brunson", "45 points", "clutch"],
        commentary=["Brunson, into the lane, the floater... good! Thirteen straight for the captain!", "Forty-five points on the road in a clincher. That ties Michael Jordan."],
        social=["JALEN BRUNSON IS THE CAPTAIN OF NEW YORK", "13 straight points in the 4th of a closeout game. MVP.", "first Knicks title since 1973 incoming"],
        social_score=0.97,
        youtube_id="k_Ou1LClcwM", youtube_start=130, youtube_end=140, source_title="JALEN BRUNSON SCORES 45 PTS IN GAME 5 TO LEAD THE KNICKS TO AN NBA CHAMPIONSHIP", source_channel="ESPN",
    ),
    DemoPlay(
        play_id="nba_okc_phx_2026_booker", league="nba", game_id="2026-01-04-OKC-PHX", date="2026-01-04",
        away="OKC", home="PHX", away_color="#007AC1", home_color="#E56020",
        away_before=105, home_before=105, away_after=105, home_after=108,
        period="Q4", clock="0:00.7", kind="three", player="Devin Booker", team="PHX",
        description="Devin Booker makes 27-foot three point pull-up jumper over Alex Caruso with 0.7 seconds left. Suns hand the NBA-best Thunder a rare loss.",
        title="BOOKER OVER CARUSO WITH 0.7 LEFT. THUNDER STREAK SNAPPED",
        rationale="Go-ahead three in the final second against the league's best team, through two elite defenders. Lead change and clutch window fired; social volume 8x baseline.",
        hype_score=0.94, base_score=0.80,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["game-winner", "Booker", "three-pointer", "clutch", "Thunder"],
        commentary=["Booker on Caruso, Dort coming to help... lets it go... GOT IT! 0.7 on the clock!", "Ajay Mitchell's corner three at the horn is off. Suns win."],
        social=["BOOK OVER CARUSO AND DORT WITH 0.7 LEFT 🥶", "the one team OKC can't figure out is the Suns", "Booker broke the slump at the perfect time"],
        social_score=0.90,
        youtube_id="rrytK3rrZII", youtube_start=51, youtube_end=61, source_title="Devin Booker CALLS GAME - EPIC ENDING Thunder vs Suns", source_channel="House of Highlights",
    ),
    DemoPlay(
        play_id="nba_phx_hou_2026_durant", league="nba", game_id="2026-01-05-PHX-HOU", date="2026-01-05",
        away="PHX", home="HOU", away_color="#E56020", home_color="#CE1141",
        away_before=97, home_before=97, away_after=97, home_after=100,
        period="Q4", clock="0:01.1", kind="three", player="Kevin Durant", team="HOU",
        description="Kevin Durant makes 28-foot three point pull-up jumper with 1.1 seconds left against his former team. Rockets 100, Suns 97.",
        title="KD BURIES HIS OLD TEAM FROM DEEP WITH 1.1 SECONDS LEFT",
        rationale="Game-winner against the team that traded him, one night after Booker's own game-winner. Revenge narrative plus clutch window pushed hype well above threshold.",
        hype_score=0.93, base_score=0.80,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["game-winner", "Durant", "revenge", "three-pointer", "clutch"],
        commentary=["Durant, inbound, Phoenix wants to trap... he pulls from deep... BANG! Durant beats his old team!", "Toyota Center is losing its mind."],
        social=["KD GAME WINNER AGAINST THE SUNS. POETRY.", "Booker last night, KD tonight. Rivalry is real.", "he said 'I never wanted to leave' and then did THAT"],
        social_score=0.89,
        youtube_id="m0qudg-vYGw", youtube_start=46, youtube_end=56, source_title="ICE COLD KEVIN DURANT - KD hits GAME-WINNING 3-POINTER vs. Suns", source_channel="ESPN",
    ),
    DemoPlay(
        play_id="nba_lal_min_2025_reaves", league="nba", game_id="2025-10-29-LAL-MIN", date="2025-10-29",
        away="LAL", home="MIN", away_color="#552583", home_color="#236192",
        away_before=114, home_before=115, away_after=116, home_after=115,
        period="Q4", clock="0:00.0", kind="buzzer", player="Austin Reaves", team="LAL",
        description="Austin Reaves takes the inbound in the backcourt, splits the pick-and-roll defense and makes a 12-foot floater at the buzzer. Lakers 116, Timberwolves 115.",
        title="REAVES COAST TO COAST FOR THE BUZZER-BEATER IN MINNESOTA",
        rationale="Walk-off floater after a 20-point Lakers lead collapsed, from a short-handed roster without LeBron or Doncic. Lead change at the horn, road crowd silenced.",
        hype_score=0.92, base_score=0.80,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["buzzer-beater", "Reaves", "floater", "game-winner"],
        commentary=["Reaves, full court, splits the double... floater... GOOD AT THE BUZZER! Lakers win!", "Twenty-point lead gone, and Austin Reaves gets it all back in one possession."],
        social=["AUSTIN REAVES IS HIM. NO LEBRON, NO LUKA, DOESN'T MATTER", "Gobert watching that floater drop 💀", "AR-15 with the walk-off in Minny"],
        social_score=0.90,
        youtube_id="AnB7HSNoDNE", youtube_start=3, youtube_end=13, source_title="Austin Reaves' CRAZY Game-Winning Floater! #TissotBuzzerBeater", source_channel="NBA",
    ),
    DemoPlay(
        play_id="nba_lal_tor_2025_hachimura", league="nba", game_id="2025-12-04-LAL-TOR", date="2025-12-04",
        away="LAL", home="TOR", away_color="#552583", home_color="#CE1141",
        away_before=120, home_before=120, away_after=123, home_after=120,
        period="Q4", clock="0:00.0", kind="buzzer", player="Rui Hachimura", team="LAL",
        description="Rui Hachimura makes 23-foot corner three point jumper at the buzzer (assist: LeBron James). Lakers 123, Raptors 120.",
        title="LEBRON GIVES IT UP, HACHIMURA WINS IT AT THE HORN IN TORONTO",
        rationale="Buzzer-beating corner three off a LeBron kick-out with the 41-year-old giving up his own shot and ending his double-digit scoring streak. Lead change at the buzzer plus a strong storyline.",
        hype_score=0.90, base_score=0.80,
        reasons=["lead_change", "clutch_time", "social_spike", "llm_viral"],
        tags=["buzzer-beater", "Hachimura", "LeBron", "corner-three"],
        commentary=["Reaves blitzed, over to LeBron, LeBron to the corner... Hachimura... BANG! At the buzzer!", "LeBron passed up the shot and ended his own scoring streak to make that play."],
        social=["RUI FROM THE CORNER AT THE BUZZER", "LeBron gave up the streak for the W. Respect.", "Toronto stunned"],
        social_score=0.87,
        youtube_id="KE7lttwMULg", youtube_start=0, youtube_end=10, source_title="Rui Hachimura Drills Corner Three For Game In Toronto #TissotBuzzerBeater", source_channel="NBA",
    ),
    DemoPlay(
        play_id="nba_min_sas_2026_wemby_12blk", league="nba", game_id="2026-05-04-MIN-SAS", date="2026-05-04",
        away="MIN", home="SAS", away_color="#236192", home_color="#C4CED4",
        away_before=102, home_before=100, away_after=102, home_after=100,
        period="Q4", clock="2:14", kind="block", player="Victor Wembanyama", team="SAS",
        description="Anthony Edwards driving layup BLOCKED by Victor Wembanyama. Wembanyama's 12th block sets the NBA single-game playoff record in Game 1 of the West semifinals.",
        title="WEMBY'S 12TH BLOCK. NOBODY HAS EVER DONE THAT IN A PLAYOFF GAME",
        rationale="Non-scoring play, but the block that set the all-time single-game playoff record, on Anthony Edwards, in a two-point game. Social burst 10x baseline; LLM override applied to a 0.20 heuristic base.",
        hype_score=0.93, base_score=0.20,
        reasons=["clutch_time", "social_spike", "llm_viral"],
        tags=["block", "playoff record", "Wembanyama", "12 blocks", "triple-double"],
        commentary=["Edwards attacks the rim... REJECTED! Wembanyama! That is his twelfth block of the night!", "Eleven points, fifteen rebounds, twelve blocks. A triple-double nobody has ever seen in the playoffs."],
        social=["WEMBY HAS 12 BLOCKS. TWELVE. IN A PLAYOFF GAME.", "Ant tried him and got sent to the third row", "the playoff block record is a Wembanyama stat now"],
        social_score=0.92,
        youtube_id="3_6UZ8XemY4", youtube_start=94, youtube_end=103, source_title="NBA PLAYOFF RECORD - Victor Wembanyama records 12 BLOCKS in Game 1 vs. Wolves", source_channel="NBA on ESPN",
    ),
    DemoPlay(
        play_id="nba_orl_det_2026_cain_poster", league="nba", game_id="2026-04-28-ORL-DET", date="2026-04-28",
        away="ORL", home="DET", away_color="#0077C0", home_color="#C8102E",
        away_before=76, home_before=74, away_after=78, home_after=74,
        period="Q4", clock="10:12", kind="dunk", player="Jamal Cain", team="ORL",
        description="Jamal Cain driving one-handed dunk over Jalen Duren. Magic go on to win Game 4 94-88 and take a 3-1 lead over the top-seeded Pistons.",
        title="JAMAL CAIN PUTS JALEN DUREN ON A POSTER IN GAME 4",
        rationale="Poster dunk by an 8-seed role player over the 1-seed's starting center, in a playoff game the underdog was about to win. Only two points, but the social burst was 11x baseline; LLM override applied.",
        hype_score=0.92, base_score=0.15,
        reasons=["social_spike", "llm_viral"],
        tags=["poster", "dunk", "playoffs", "Jamal Cain", "8-seed"],
        commentary=["Cain, baseline, rises... OH! ON DUREN! Jamal Cain just put him on the floor!", "The eighth seed is one win from knocking out the one seed, and they're doing it with plays like that."],
        social=["JAMAL CAIN JUST ENDED JALEN DUREN'S SEASON AND HIS LIFE", "who is Jamal Cain and why is he dunking like that", "poster of the playoffs and it's not close"],
        social_score=0.93,
        youtube_id="5waa0WrLhlk", youtube_start=1, youtube_end=11, source_title="POSTER OF THE YEAR??? Jamal Cain POSTERIZES Jalen Duren in Game 4 | NBA on ESPN", source_channel="ESPN",
    ),
    # ------------------------------------------------------------------ NFL 2025
    DemoPlay(
        play_id="nfl_gb_chi_2025_walkoff", league="nfl", game_id="2025-12-20-GB-CHI", date="2025-12-20",
        away="GB", home="CHI", away_color="#203731", home_color="#C83200",
        away_before=16, home_before=16, away_after=16, home_after=22,
        period="OT", clock="4:50", kind="deep_pass", player="DJ Moore", team="CHI",
        description="C.Williams pass deep left to D.Moore for 46 yards, TOUCHDOWN. Bears walk off the Packers in overtime after scoring 10 points in the final 1:59 of regulation.",
        title="CALEB TO DJ MOORE FOR 46. BEARS WALK OFF THE PACKERS IN OT",
        rationale="Walk-off overtime touchdown in a first-place division rivalry after a 10-point comeback in the final two minutes. Voted the NFL's Moment of the Year; every heuristic fired.",
        hype_score=0.98, base_score=0.95,
        reasons=["lead_change", "big_scoring_play", "momentum_swing", "clutch_time", "social_spike", "llm_viral"],
        tags=["walk-off", "overtime", "Bears-Packers", "Caleb Williams", "touchdown"],
        commentary=["Williams steps up, launches it down the middle... Moore! Touchdown! Bears win! Bears win!", "From 16-6 down with two minutes left to a walk-off in overtime."],
        social=["CALEB WILLIAMS TO DJ MOORE. BEARS WALK OFF THE PACKERS. CINEMA.", "Soldier Field is shaking", "16-6 with two minutes left. Are you kidding me"],
        social_score=0.98,
        youtube_id="cbf64unxDmI", youtube_start=1, youtube_end=11, source_title="BEARS TAKE DOWN PACKERS - Caleb Williams finds DJ Moore for GAME-WINNING 46-yard TD", source_channel="NFL on FOX",
        occurred_utc="2025-12-21T04:29:15Z",
        play_by_play_url="https://www.espn.com/nfl/playbyplay/_/gameId/401772613",
    ),
    DemoPlay(
        play_id="nfl_den_was_2025_burks", league="nfl", game_id="2025-11-30-DEN-WAS", date="2025-11-30",
        away="DEN", home="WAS", away_color="#FB4F14", home_color="#5A1414",
        away_before=13, home_before=7, away_after=13, home_after=14,
        period="Q3", clock="9:58", kind="deep_pass", player="Treylon Burks", team="WAS",
        description="M.Mariota pass short right to T.Burks for 5 yards, TOUCHDOWN. One-handed catch over R.Moss on 3rd and goal.",
        title="TREYLON BURKS GOES FULL OBJ WITH A ONE-HANDED TOUCHDOWN ON SUNDAY NIGHT",
        rationale="One-handed fade-route touchdown catch on Sunday Night Football, wearing number 13, immediately compared to Odell Beckham's 2014 grab. The 'one-handed' pattern is the strongest virality predictor in the index.",
        hype_score=0.93, base_score=0.35,
        reasons=["big_scoring_play", "social_spike", "llm_viral"],
        tags=["one-handed-catch", "catch-of-the-year", "Burks", "SNF", "touchdown"],
        commentary=["Mariota, fade to the corner... Burks... ONE HAND! Unbelievable!", "Number 13, one hand, on Sunday night. Where have we seen that before?"],
        social=["TREYLON BURKS JUST DID THE OBJ CATCH IN THE SAME NUMBER", "OBJ himself is tweeting about it", "catch of the year and it's not close"],
        social_score=0.92,
        youtube_id="DKJB1y7ZzHQ", youtube_start=2, youtube_end=12, source_title="'UNBELIEVABLE!' Treylon Burks makes one-handed touchdown catch vs. Broncos | SNF", source_channel="NFL on NBC",
        occurred_utc="2025-12-01T03:04:19Z",
        play_by_play_url="https://www.espn.com/nfl/playbyplay/_/gameId/401772931",
    ),
    DemoPlay(
        play_id="nfl_sf_sea_2026_shaheed", league="nfl", game_id="2026-01-17-SF-SEA", date="2026-01-17",
        away="SF", home="SEA", away_color="#AA0000", home_color="#69BE28",
        away_before=0, home_before=0, away_after=0, home_after=7,
        period="Q1", clock="14:47", kind="deep_pass", player="Rashid Shaheed", team="SEA",
        description="Opening kickoff returned by R.Shaheed for 95 yards, TOUCHDOWN. Sixth opening-kickoff return touchdown in NFL playoff history.",
        title="SHAHEED TAKES THE OPENING KICKOFF 95 YARDS. SEATTLE UP IN 13 SECONDS",
        rationale="Opening-kickoff return touchdown in a divisional playoff game against a division rival, only the sixth ever. Big scoring play plus playoff stakes plus instant social burst.",
        hype_score=0.92, base_score=0.55,
        reasons=["big_scoring_play", "momentum_swing", "social_spike", "llm_viral"],
        tags=["kickoff-return", "playoffs", "Shaheed", "touchdown", "Seahawks"],
        commentary=["Shaheed takes it at the five... finds the seam... nobody is catching him! Touchdown Seattle on the opening kickoff!", "Thirteen seconds into the divisional round and Lumen Field is unhinged."],
        social=["SHAHEED 95 YARDS ON THE OPENING KICK 😱", "Niners haven't run an offensive play and they're down 7", "Lumen Field just hit a new decibel record"],
        social_score=0.91,
        youtube_id="PbNLJJD6-KM", youtube_start=7, youtube_end=17, source_title="Seahawks' Rashid Shaheed takes opening kickoff 95 YARDS for a TOUCHDOWN vs. 49ers", source_channel="NFL on FOX",
        occurred_utc="2026-01-18T01:21:27Z",
        play_by_play_url="https://www.espn.com/nfl/playbyplay/_/gameId/401772984",
    ),
    DemoPlay(
        play_id="nfl_lar_chi_2026_kmet", league="nfl", game_id="2026-01-18-LAR-CHI", date="2026-01-18",
        away="LAR", home="CHI", away_color="#003594", home_color="#C83200",
        away_before=17, home_before=10, away_after=17, home_after=17,
        period="Q4", clock="0:18", kind="deep_pass", player="Caleb Williams", team="CHI",
        description="4th down. C.Williams scrambles back to the 40-yard line, escapes pressure and throws to C.Kmet for a TOUCHDOWN. Game tied 17-17; Rams win 20-17 in overtime.",
        title="CALEB RETREATS 30 YARDS AND THROWS A MIRACLE TO KMET ON 4TH DOWN",
        rationale="Fourth-down, season-on-the-line scramble touchdown to force overtime in the divisional round. Even in a losing effort, the play's absurdity drove a 12x social spike.",
        hype_score=0.95, base_score=0.55,
        reasons=["big_scoring_play", "clutch_time", "social_spike", "llm_viral"],
        tags=["playoffs", "4th down", "Caleb Williams", "miracle", "touchdown"],
        commentary=["Fourth down, season on the line... Williams running for his life... back to the 40... throws it up... KMET! TOUCHDOWN!", "One of the most improbable throws you will ever see."],
        social=["CALEB WILLIAMS IS A MAGICIAN", "he was at the 40 yard line on a play from the 10", "Bears lost but that throw lives forever"],
        social_score=0.96,
        youtube_id="1DgjDi774FI", youtube_start=10, youtube_end=20, source_title="Caleb Williams makes MIRACULOUS throw to Cole Kmet to tie it late for Bears", source_channel="NFL on NBC",
        occurred_utc="2026-01-19T02:35:25Z",
        play_by_play_url="https://www.espn.com/nfl/playbyplay/_/gameId/401772985",
    ),
    DemoPlay(
        play_id="nfl_sea_ne_sb60_nwosu", league="nfl", game_id="2026-02-08-SEA-NE", date="2026-02-08",
        away="SEA", home="NE", away_color="#69BE28", home_color="#002244",
        away_before=22, home_before=7, away_after=29, home_after=7,
        period="Q4", clock="4:27", kind="pick_six", player="Uchenna Nwosu", team="SEA",
        description="D.Maye pass short middle INTERCEPTED by U.Nwosu at NE 45. U.Nwosu for 45 yards, TOUCHDOWN. Seahawks 29, Patriots 7 in Super Bowl LX.",
        title="NWOSU PICK SIX SEALS SUPER BOWL LX FOR SEATTLE",
        rationale="Super Bowl pick six off a cornerback blitz that turned a two-score game into a rout. Big scoring play and 14-point swing on the biggest stage.",
        hype_score=0.95, base_score=0.85,
        reasons=["big_scoring_play", "momentum_swing", "social_spike", "llm_viral"],
        tags=["Super Bowl", "pick-six", "Nwosu", "Seahawks", "touchdown"],
        commentary=["Maye under pressure, throws... intercepted! Nwosu! He's going to take it to the house! Touchdown Seattle!", "That is the dagger in Super Bowl LX."],
        social=["NWOSU PICK SIX. SUPER BOWL OVER.", "Seattle's defense just put on a clinic", "Maye is going to see that blitz in his sleep"],
        social_score=0.93,
        youtube_id="IuD7uPAALV0", youtube_start=1, youtube_end=11, source_title="Seahawks Uchenna Nwosu SB 60 Pick Six.", source_channel="Sea Hawks Videos",
        occurred_utc="2026-02-09T02:58:03Z",
        play_by_play_url="https://www.espn.com/nfl/playbyplay/_/gameId/401772988",
    ),
]


def get_play(play_id: str) -> DemoPlay:
    for p in PLAYS:
        if p.play_id == play_id:
            return p
    raise KeyError(play_id)
