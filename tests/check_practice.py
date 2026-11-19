"""Browser acceptance check. Pass the base URL of the running static page."""
import os
import sys
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright


def draw(page, points):
    page.mouse.move(*points[0])
    page.mouse.down()
    for point in points[1:]:
        page.mouse.move(*point)
    page.mouse.up()


def trace_character(page, char):
    strokes = page.evaluate("""char => {
        const g = document.querySelector('#cell svg g[transform]');
        return dataByLetter.get(char).medians.map(line => line.map(([x,y]) => {
            const p = new DOMPoint(x,y).matrixTransform(g.getScreenCTM());
            return [p.x,p.y];
        }));
    }""", char)
    for points in strokes:
        draw(page, points)


def check(base):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH") or None)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(base + "?" + urlencode({"word": "人〇大", "speed": "fast"}))
        page.wait_for_selector("#start:enabled")
        assert "〇" in page.locator("#notice").inner_text()
        assert page.locator("#count").input_value() == "3"
        page.locator("#replay").click()
        page.locator("#count").select_option("2")
        page.reload()
        assert page.locator("#count").input_value() == "2"
        page.locator("#start").click()
        for number in (0, 1, 2):
            for char in "人大":
                page.wait_for_function("([n, c]) => pass === n && writer?._char === c && writer._quiz?._isActive",
                                       arg=[number, char])
                assert page.evaluate("writer._renderState.state.character.outline.opacity") == int(number == 0)
                assert page.evaluate("writer._quiz._currentStrokeIndex") == 0
                if number == 1:
                    box = page.locator("#cell").bounding_box()
                    x, y = box["x"] + 5, box["y"] + 5
                    for _ in range(3):
                        draw(page, [(x, y), (x + 20, y)])
                    assert page.evaluate("writer._quiz._mistakesOnStroke") == 3
                    page.wait_for_function("writer._renderState.state.character.highlight.strokes[0].opacity > 0")
                trace_character(page, char)
        page.wait_for_selector("#start:visible")
        page.locator("#start").click()
        page.wait_for_function("pass === 0 && writer._char === '人' && writer._quiz?._isActive")
        page.goto(base + "?" + urlencode({"word": "人"}))
        page.locator("#count").select_option("1")
        page.locator("#start").click()
        for number in (0, 1):
            page.wait_for_function("n => pass === n && writer?._quiz?._isActive", arg=number)
            trace_character(page, "人")
        page.wait_for_selector("#start:visible")
        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    check(sys.argv[1])
    print("Practice OK")
