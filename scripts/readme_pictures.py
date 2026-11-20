"""Make the README pictures from the real app, with neutral content only.

    .venv\\Scripts\\python.exe scripts\\readme_pictures.py

Windows speech synthesis reads a short neutral talk into a sound file; the app runs with a throwaway data folder
(nothing of the user's own work is ever on screen) and transcribes it; a headless Chromium-based browser opens
the window's pages over its debugging protocol and saves each picture into docs/images/. Every cache and the
throwaway folder stay under .cache/. The browser and the app are stopped at the end, whatever happens.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parakeet_stt import env  # noqa: E402 - the project's own environment comes first

env.apply()

import websockets  # noqa: E402

from parakeet_stt.server.browser import find_chromium  # noqa: E402

WORK = ROOT / ".cache" / "readme-pictures"
OUT = ROOT / "docs" / "images"
APP_PORT = 8796
CDP_PORT = 9336
BASE = f"http://127.0.0.1:{APP_PORT}"
WIDTH, HEIGHT = 1280, 820

TALK = """Good evening, and welcome to tonight's talk. Tonight we will look at one simple question. What does it mean
to really listen? Most of the time, when someone speaks to us, we are already preparing our answer. We listen to a
few words, we decide what the other person means, and then we wait for our turn. Tonight I would like to
suggest a different approach.

Listening is a skill, and like every skill, it can be learned. The first step is very modest. When you notice
that you have stopped following, simply come back to the voice you are hearing. Nobody listens perfectly, and
that is fine.

The second step is curiosity. Instead of deciding in advance what someone will say, we can stay open for a little
longer. We let the sentence finish. We notice the tone of the voice, the pauses, the small hesitations. Often the
most important part of a message is not in the words at all."""

SPEAK = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$names = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }
foreach ($v in @("Microsoft Mark", "Microsoft David", "Microsoft Zira")) {  # the most natural first
    if ($names -contains $v) { $s.SelectVoice($v); break }
}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($args[1], $fmt)
$s.Speak([System.IO.File]::ReadAllText($args[0], [System.Text.Encoding]::UTF8))
$s.Dispose()
"""


def sample() -> Path:
    wav = WORK / "evening-talk.wav"
    text, script = WORK / "talk.txt", WORK / "speak.ps1"
    if wav.is_file() and text.is_file() and text.read_text(encoding="utf-8") == TALK:
        return wav  # spoken from the same text before
    text.unlink(missing_ok=True)  # written again only once the sound is whole: a broken run is never reused
    script.write_text(SPEAK, encoding="utf-8")
    spoken = WORK / "talk-text.part"
    spoken.write_text(TALK, encoding="utf-8")
    part = wav.with_name(wav.stem + ".part.wav")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), str(spoken),
                    str(part)], check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    part.replace(wav)
    spoken.replace(text)
    return wav


def get(path: str) -> Any:
    return json.loads(urllib.request.urlopen(f"{BASE}{path}", timeout=10).read())


def post(path: str, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": BASE})  # fmt: skip
    return json.loads(urllib.request.urlopen(req, timeout=30).read() or b"null")


def wait_for(cond: Any, timeout: float, what: str) -> Any:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            got = cond()
        except OSError:
            got = None
        if got:
            return got
        time.sleep(0.5)
    raise SystemExit(f"timed out waiting for {what}")


def start_app() -> subprocess.Popen[bytes]:
    data = WORK / "data"
    shutil.rmtree(data, ignore_errors=True)  # a fresh, empty app every time
    app_env = dict(os.environ, PARAKEET_STT_DATA=str(data), PARAKEET_STT_PORT=str(APP_PORT))
    return subprocess.Popen([sys.executable, "-X", "utf8", "-m", "parakeet_stt", "app", "--no-window"], cwd=ROOT,
                            env=app_env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)  # fmt: skip


class Page:
    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.seq = 0

    async def call(self, method: str, **params: object) -> dict[str, Any]:
        self.seq += 1
        my = self.seq
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return dict(msg["result"])

    async def js(self, body: str) -> Any:
        r = await self.call("Runtime.evaluate", expression=f"(async () => {{ {body} }})()", awaitPromise=True,
                            returnByValue=True)  # fmt: skip
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:800])
        return r["result"].get("value")

    async def until(self, condition: str, what: str, timeout: float = 20) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if await self.js(f"return Boolean({condition});"):
                return
            await asyncio.sleep(0.2)
        raise SystemExit(f"the page never showed {what}")

    async def shot(self, name: str, height: int = HEIGHT) -> None:
        await asyncio.sleep(0.5)  # transitions
        img = await self.call("Page.captureScreenshot", format="png",
                              clip={"x": 0, "y": 0, "width": WIDTH, "height": height, "scale": 1})  # fmt: skip
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / name).write_bytes(base64.b64decode(img["data"]))
        print("picture:", (OUT / name).relative_to(ROOT))


async def pictures(wav: Path) -> None:
    exe = find_chromium()
    if exe is None:
        raise SystemExit("no Chromium-based browser (Edge, Chrome, Brave) found")
    profile = WORK / "browser"
    shutil.rmtree(profile, ignore_errors=True)
    browser = subprocess.Popen([str(exe), "--headless=new", f"--remote-debugging-port={CDP_PORT}",
                                f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                                "--hide-scrollbars", "--force-device-scale-factor=1", "about:blank"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        targets = wait_for(lambda: json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json",
                                                                     timeout=1).read()), 20, "the browser")  # fmt: skip
        target = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
            p = Page(ws)
            await p.call("Page.enable")
            await p.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1,
                         mobile=False)  # fmt: skip
            await p.call("Page.navigate", url=f"{BASE}/#queue")
            await p.until("document.querySelector('.machine')", "the queue")
            job = post("/api/jobs", {"path": str(wav)})
            # the lecture while it is being transcribed: progress, position and time left
            await p.until("Number(document.querySelector('.job progress.bar')?.value) >= 40 && "
                          "!document.querySelector('.job .detail-pos')?.textContent.startsWith('0:00')",
                          "the progress", 120)  # fmt: skip
            await p.shot("queue.png")
            await p.until("document.querySelector('.job .badge-done')", "the finished lecture", 600)
            wait_for(lambda: (next(j for j in get("/api/status")["jobs"] if j["id"] == job["id"]).get("preview") or {})
                     .get("state") == "ready", 300, "the preview")  # fmt: skip
            job_id = job["id"]

            await p.call("Page.navigate", url=f"{BASE}/#edit/{job_id}/cz-template")
            await p.until("document.querySelectorAll('.cue-row').length > 3 && "
                          "document.querySelector('.wave-host')?.childElementCount > 0", "the editor")  # fmt: skip
            await p.js("const c = document.querySelectorAll('.cue-row')[2]; c.scrollIntoView({block: 'center'});"
                       "c.click(); return true;")  # fmt: skip
            await p.shot("editor.png")
            await p.js("document.querySelector('button[aria-label=\"Dark look\"]').click(); return true;")
            await p.until("document.documentElement.dataset.look === 'dark'", "the dark look", 5)
            await p.shot("editor-dark.png")
            await p.js("document.querySelector('button[aria-label=\"Light look\"]').click(); return true;")

            await p.call("Page.navigate", url=f"{BASE}/#help")
            await p.until("document.querySelector('.help')", "the help page")
            # the picture ends above the terms part: its examples are words of one subject
            terms = (
                "[...document.querySelectorAll('.help-part')]"
                ".find((e) => e.querySelector('h2')?.textContent === 'Terms and names')"
            )
            await p.until(terms, "the help page's terms part")
            # ... and below the last part that fits whole, so no card is cut in two
            bottom = await p.js(
                f"const limit = Math.min({terms}.getBoundingClientRect().top, {HEIGHT});"
                "const ends = [...document.querySelectorAll('.help-part, .guide-steps li')]"
                ".map((e) => e.getBoundingClientRect().bottom)"
                ".filter((b) => b <= limit); return ends.length ? Math.max(...ends) : limit;"
            )
            await p.shot("help.png", height=int(bottom) + 16)
    finally:
        browser.kill()
        browser.wait(10)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    wav = sample()
    app = start_app()
    try:
        wait_for(lambda: get("/api/status"), 60, "the app")
        wait_for(lambda: (get("/api/status").get("models") or {}).get("state") == "ready", 1800, "the speech model")
        # the speed test runs by itself on a fresh start; the lecture is added once it is over
        wait_for(lambda: (get("/api/status").get("machine") or {}).get("per_s") is not None, 300, "the speed test")
        asyncio.run(pictures(wav))
    finally:
        app.kill()
        app.wait(10)


if __name__ == "__main__":
    main()
