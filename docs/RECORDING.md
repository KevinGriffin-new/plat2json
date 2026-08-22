# Recording The Plat Parsing Animation

Open `plat-parsing-animation.html`, `subdivision-parsing-animation.html`, or
`area-thirty3-parsing-animation.html` in a current Chromium, Edge, or Firefox
browser. Select its **Record** button, wait for the loop to finish, then select
**Download recording**. The parcel animation records 20 seconds; the two
subdivision studies record 30 seconds. The browser captures the canvas, so the
downloaded video exactly matches the interactive animation.

To produce an MP4 for social or presentation software, convert the downloaded
WebM locally:

```powershell
ffmpeg -i plat2json-parsing.webm -c:v libx264 -pix_fmt yuv420p plat2json-parsing.mp4
```

The generic animations are stylized reconstructions and do not upload a plan or
call a vision model. The Area Thirty3 study is based on a specific public plat,
but its drawing is still an explanatory reconstruction rather than a traced or
validated extraction. The production pipeline remains the authority for actual
extraction and validation.
