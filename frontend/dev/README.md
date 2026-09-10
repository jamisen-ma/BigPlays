# dev tools

`embedtest.html` — checks which YouTube videos actually play inside an embed on this origin
(the league channels block third-party embeds with error 150). Copy it into `public/`, edit the
`CANDS` list, open http://localhost:5173/embedtest.html, and read the PLAYS / ERROR column.
Remove it from `public/` afterwards so it is not shipped in `dist/`.
