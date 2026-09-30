import asyncio
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFont

from bigplays.ingest.plays import read_scorebug
from bigplays.media.ffmpeg_utils import ffmpeg_bin
from bigplays.media.scoreboard import frame_clock, tesseract_rows


def fixture(words):
    header = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
    page = '1\t1\t0\t0\t0\t0\t0\t0\t1000\t500\t-1\t\n'
    return header + page + ''.join(
        f'5\t1\t{block}\t1\t1\t{i}\t{x}\t400\t{width}\t20\t{conf}\t{text}\n'
        for i, (block, x, width, conf, text) in enumerate(words, 1))


def test_linux_ocr_preserves_clock_teams_and_coordinates():
    rows = tesseract_rows(fixture([
        (1, 100, 100, 95, 'OHIO'), (1, 210, 100, 94, 'STATE'),
        (2, 400, 80, 96, '8:28'), (3, 500, 60, 97, '2ND'),
        (4, 650, 150, 93, 'GEORGIA'),
    ]))
    assert rows[0]['text'] == 'OHIO STATE'
    assert rows[0]['y'] == pytest.approx(.16)
    assert rows[0]['width'] == pytest.approx(.21)
    assert read_scorebug(rows, [['OHIO STATE'], ['GEORGIA']]) == {
        'period': 2, 'clock_seconds': 508, 'clock': '8:28'}
    assert read_scorebug(rows, [['TEXAS'], ['GEORGIA']]) is None


def test_linux_ocr_rejects_low_confidence_clock_and_down_marker():
    words = [(1, 100, 120, 95, 'TEXAS'), (2, 400, 80, 55, '8:28'),
             (3, 500, 60, 97, '2ND'), (4, 650, 150, 93, 'GEORGIA')]
    assert read_scorebug(tesseract_rows(fixture(words)), [['TEXAS'], ['GEORGIA']]) is None
    words[1] = (2, 400, 80, 96, '8:28')
    words.extend([(3, 560, 10, 96, '&'), (3, 580, 20, 95, '5')])
    assert read_scorebug(tesseract_rows(fixture(words)), [['TEXAS'], ['GEORGIA']]) is None


@pytest.mark.skipif(not shutil.which('tesseract'), reason='Tesseract is installed in the cloud image')
def test_linux_video_frame_clock(tmp_path):
    image = Image.new('RGB', (1280, 720), 'black')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    for text, x in [('TEXAS', 60), ('GEORGIA', 350), ('2ND', 650), ('8:28', 800)]:
        draw.text((x, 600), text, font=font, fill='white')
    still = tmp_path / 'scoreboard.png'
    image.save(still)
    video = tmp_path / 'scoreboard.ts'
    subprocess.run([ffmpeg_bin(), '-v', 'error', '-loop', '1', '-i', str(still),
                    '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-f', 'mpegts', str(video)], check=True)
    rows = asyncio.run(frame_clock(video, 1, 'tesseract'))
    assert read_scorebug(rows, [['TEXAS'], ['GEORGIA']]) == {
        'period': 2, 'clock_seconds': 508, 'clock': '8:28'}
