import hashlib
import io
import json
import os
import re
import tempfile
from pathlib import Path

STYLE = "black-96-slow-v3"
ASSETS = Path(__file__).resolve().parents[1] / "assets"


def render(word, cache):
    from PIL import Image
    from playwright.sync_api import sync_playwright

    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((STYLE + word).encode()).hexdigest()
    gif = cache / (key + ".gif")
    if gif.exists():
        return gif
    data = [json.loads((ASSETS / "characters" / (c + ".json")).read_text(encoding="utf-8"))
            if "\u3400" <= c <= "\u9fff" else None for c in word]
    frames = []
    durations = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 96 * len(word), "height": 96})
        page.set_content('<body style="margin:0;display:flex;background:white"></body>')
        page.clock.install(time="2025-01-01T00:00:00Z")
        page.clock.pause_at("2025-01-01T00:00:01Z")
        page.add_script_tag(path=str(ASSETS / "hanzi-writer.min.js"))
        page.evaluate("""async ([word, data]) => {
            window.writers = Array.from(word, (char, i) => {
                let el = document.body.appendChild(document.createElement("div"));
                if (!data[i]) {
                    el.style.cssText = "width:96px;text-align:center;font:48px/96px sans-serif";
                    el.textContent = char;
                    return null;
                }
                return HanziWriter.create(el, char, {width:96,height:96,padding:5,showCharacter:false,
                    showOutline:true,strokeAnimationSpeed:1,delayBetweenStrokes:360,charDataLoader:(_, resolve)=>resolve(data[i])});
            });
            await Promise.all(writers.filter(Boolean).map(w => w.getCharacterData()));
        }""", [word, data])
        def capture(duration):
            with Image.open(io.BytesIO(page.screenshot())) as shot:
                frames.append(shot.convert("P", palette=Image.Palette.ADAPTIVE))
            durations.append(duration)

        capture(500)
        for i, char in enumerate(data):
            if char is None:
                continue
            page.evaluate("""i => {
                window.finished = false;
                writers[i].animateCharacter().then(() => { window.finished = true; });
            }""", i)
            for _ in range(30 * len(char["strokes"]) + 20):
                page.clock.run_for(90)
                # Play each 90 ms of brush movement over 180 ms in the GIF.
                capture(180)
                if page.evaluate("window.finished"):
                    durations[-1] += 800
                    break
            else:
                raise TimeoutError("Hanzi Writer не завершил анимацию")
        browser.close()
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".gif", delete=False) as tmp:
        temp = Path(tmp.name)
    try:
        durations[-1] += 1200
        frames[0].save(temp, save_all=True, append_images=frames[1:], duration=durations, loop=0)
        os.replace(temp, gif)
    finally:
        temp.unlink(missing_ok=True)
    return gif


def upload(client, owner, path):
    import requests

    docs = client.get_api().docs
    server = docs.getMessagesUploadServer(peer_id=owner, type="doc")
    try:
        with path.open("rb") as source:
            response = requests.post(server["upload_url"], timeout=30,
                                     files={"file": ("strokes.gif", source, "image/gif")})
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        info = f", HTTP {status}" if status is not None else ""
        raise RuntimeError(f"Сбой загрузки GIF в ВК ({type(error).__name__}{info}).") from None
    token = payload.get("file") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token.strip():
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            error = error.get("error_code")
        codes = [error]
        if isinstance(payload, dict):
            codes.extend(payload.get(k) for k in ("error_descr", "error_code"))
        labels = [str(code) for code in codes if code is not None and
                  re.fullmatch(r"(?:-?\d{1,8}|[a-zA-Z]{1,16}(?:_[a-zA-Z0-9]{1,16}){1,5})", str(code))]
        fields = ", ".join(k for k in ("file", "error", "error_code", "error_descr", "response")
                           if isinstance(payload, dict) and k in payload)
        raise ValueError(f"Загрузка GIF: HTTP {response.status_code}, код ВК: {', '.join(dict.fromkeys(labels)) or 'не указан'}, "
                         f"поля ответа: {fields or 'нераспознанные'}, размер: {path.stat().st_size} байт.")
    saved = docs.save(file=token, title="strokes.gif")
    doc = saved["doc"]
    attachment = f"doc{doc['owner_id']}_{doc['id']}"
    if doc.get("access_key"):
        attachment += "_" + doc["access_key"]
    return attachment

