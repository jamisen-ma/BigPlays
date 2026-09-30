"""Export reviewed source-play links with supporting source and OCR evidence."""
import json
from pathlib import Path
from bigplays.ingest.week3_archive import CACHE, game_sources

items = json.loads((CACHE / 'nfl-single-plays.json').read_text())
reviewed = json.loads((CACHE / 'reviewed-index-matches.json').read_text())
plays = {p['id']: p for _, rows in game_sources().values() for p in rows}
# These clips are identifiable, but ESPN bundles their event into the preceding
# touchdown; that timestamp cannot be called the exact conversion/PAT event time.
bundled = {170: '4018729543619', 203: '4018729494259', 233: '401872961788',
           272: '4018729603063', 313: '4018729622890', 322: '4018729623356'}
notes = {
    38: 'NFL described 24 yards; ESPN final statistics record 23. Unique opening-drive third-down reception to Dohnte Meyers.',
    62: 'NFL described a 9-yard sack; ESPN records 10 yards and a defensive penalty nullifying the play. Unique Jordyn Brooks sack of Mahomes in the red zone.',
    70: 'Video includes the earlier nullified sack before Simmons actual sack. Matched the highlighted Simmons sack, not the earlier penalty play.',
    103: 'Corrected automatic false match: within the 10-yard line describes destination, not gain. Broadcast Q2 2:23–2:19 confirms 17-yard Sanders rush at Q2 2:21.',
    107: 'NFL initial description says 20 yards; ESPN final statistics record 19. Source thumbnail Q2 6:29 and second down confirm Q2 6:26.',
    171: 'Separate source video frame at 2 seconds shows bottom scoreboard fourth quarter 4:45, second down. Ignore top-left score from another game.',
    185: 'NFL initial description says 17 yards; final ESPN statistics record 18. Unique late-game third-down Tony Pollard reception of this length.',
    196: 'NFL says 6-yard sack; final ESPN records 5. Unique Leonard Williams sack of Marcus Mariota in this game.',
    201: 'NFL says 37-yard field goal; ESPN final statistics record 36. Scoring context and quarter identify this field goal uniquely.',
    213: 'NFL says 25-yard gain; final ESPN statistics record 26. First Jordan Watkins regular-season reception uniquely identifies the play.',
    240: 'NFL headline says 51 yards, but source thumbnail explicitly shows 41 YD ATTEMPT, second quarter :27. Matches ESPN Q2 :23 41-yard field goal.',
    253: 'Source video frame at 5 seconds shows second quarter 0:19, third-and-14; frame8 shows0:16. Matches ESPN Q2 :19. Bowers stat banner also shows prior6 receptions for72yards, agreeing with previous ESPN plays. NFL initial yardage13 differs from final ESPN15.',
}
mapping, evidence = {}, []
# A separate review checked every automatic match against the source description,
# then inspected footage for wording discrepancies. Manual corrections below win.
automatic_review = CACHE / 'automatic-match-review.json'
if automatic_review.exists():
    for audited in json.loads(automatic_review.read_text()):
        mapping[audited['id']] = audited['match']
        evidence.append({'video_id': audited['id'], 'play_id': audited['match'],
                         'method': 'Independent review of automatic source-play match',
                         'audit': audited})
for i, play_id in reviewed.items():
    item = items[int(i)]
    play = plays[play_id] if play_id else None
    mapping[item['id']] = play_id
    row = {'video_id': item['id'], 'source_url': item['source_url'],
           'title': item['title'], 'description': item.get('description'),
           'play_id': play_id, 'method': 'Reviewed NFL description against ESPN play-by-play',
           'game_id': play_id[:9] if play_id else None,
           'play_text': play['text'] if play else None,
           'quarter': play['period']['number'] if play else None,
           'clock': play['clock']['displayValue'] if play else None,
           'wallclock': play.get('wallclock') if play else None}
    if int(i) in notes:
        row['review_note'] = notes[int(i)]
    ocr = CACHE / 'ocr' / ('nfl_' + item['id'] + '.json')
    thumbnail = CACHE / 'thumbnails' / (item['id'] + '.json')
    if ocr.exists():
        row['broadcast_scoreboard_ocr'] = [{'offset': f['offset'], 'text': [r['text'] for r in f['rows']]} for f in json.loads(ocr.read_text())]
    if thumbnail.exists():
        row['source_thumbnail_ocr'] = [r['text'] for r in json.loads(thumbnail.read_text()) if r['y'] < .35]
    priority = CACHE / 'priority_frames' / (item['id'] + '.json')
    if priority.exists():
        row['source_video_frame_at_2_seconds'] = [r['text'] for r in json.loads(priority.read_text())]
    for extra in (5, 8, 12):
        priority_extra = CACHE / 'priority_frames' / (item['id'] + f'-{extra}.json')
        if priority_extra.exists():
            row[f'source_video_frame_at_{extra}_seconds'] = [r['text'] for r in json.loads(priority_extra.read_text())]
    evidence = [e for e in evidence if e['video_id'] != item['id']]
    evidence.append(row)
for i, bundled_id in bundled.items():
    item = items[i]
    mapping[item['id']] = None
    evidence.append({'video_id': item['id'], 'source_url': item['source_url'], 'title': item['title'], 'play_id': None,
                     'bundled_play_id': bundled_id, 'method': 'No independent event wallclock: ESPN bundles conversion/PAT with preceding touchdown.'})
for path in [CACHE / 'play-matches.json', Path('bigplays/ingest/week3_matches.json')]:
    path.write_text(json.dumps(mapping, indent=2) + '\n')
Path('bigplays/ingest/week3_match_evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(f'{sum(v is not None for v in mapping.values())} reviewed matches; {sum(v is None for v in mapping.values())} explicitly unresolved')
