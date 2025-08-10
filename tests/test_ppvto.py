from bigplays.ingest.ppvto import extract_hls_from_html


def test_extract_hls_from_html_basic():
    html = """
    <html><body>
      <video controls>
        <source src="https://cdn.example.com/stream/master.m3u8" type="application/vnd.apple.mpegurl" />
      </video>
    </body></html>
    """
    assert extract_hls_from_html(html) == "https://cdn.example.com/stream/master.m3u8"


def test_extract_hls_from_html_regex():
    html = "<script>var u='https://cdn.example.com/x.m3u8?token=abc';</script>"
    assert extract_hls_from_html(html).startswith("https://cdn.example.com/x.m3u8")

