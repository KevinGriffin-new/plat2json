# Recording The Plat Parsing Animation

Open `plat-parsing-animation.html` in a current Chromium, Edge, or Firefox
browser. Select **Record 20 second WebM**, wait for the loop to finish, then
select **Download recording**. The browser captures the Three.js canvas, so the
downloaded video exactly matches the interactive animation.

To produce an MP4 for social or presentation software, convert the downloaded
WebM locally:

```powershell
ffmpeg -i plat2json-parsing.webm -c:v libx264 -pix_fmt yuv420p plat2json-parsing.mp4
```

The animation is a generic, stylized reconstruction of a survey plan. It does
not upload a plan or call a vision model. The production pipeline remains the
authority for actual extraction and validation.
