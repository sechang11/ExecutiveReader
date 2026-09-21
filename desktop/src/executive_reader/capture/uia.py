"""Read text out of any window through Windows UI Automation.

This is the rung that makes the app useful without screenshots. UI Automation
is the same layer screen readers use, so Electron apps, Office and native
Windows apps expose their full text through it, including the parts scrolled
out of view. No capturing, no scrolling, no recognition errors.

Chrome is the exception worth knowing about. It builds a renderer
accessibility tree only when it is started with --force-renderer-accessibility;
without that flag it exposes its toolbar and nothing of the page. See
chrome_accessibility_state() and the note in the README.

COM is initialised once at each public entry point. Nested initialisation
silently breaks tree traversal, so the private helpers never do it themselves.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

from ..document import Document

_TIME_BUDGET = 8.0
_MAX_DEPTH = 30
_MAX_NODES = 6000
_MIN_USEFUL = 40

_TEXT_CONTROLS = {"TextControl", "DocumentControl", "EditControl",
                  "HyperlinkControl", "ListItemControl", "HeadingControl"}
_SKIP_CONTROLS = {"ScrollBarControl", "TitleBarControl", "MenuBarControl"}

# Browser and shell furniture that otherwise gets read out before the article.
_CHROME_NOISE = {
    "address and search bar", "create split view", "search or enter address",
    "address field", "back", "forward", "reload", "home", "bookmark this tab",
    "new tab", "close", "minimize", "maximize", "restore", "extensions",
    "chrome", "you", "search tabs", "all bookmarks", "apps", "tab groups",
    "customize and control google chrome", "bookmarks", "menu",
}


class UIAUnavailable(RuntimeError):
    pass


@dataclass
class WindowInfo:
    title: str = ""
    url: str = ""
    app: str = ""


def _auto():
    try:
        import uiautomation
    except ImportError as exc:
        raise UIAUnavailable(
            "uiautomation is not installed. Run: pip install uiautomation") from exc
    return uiautomation


def _safe_name(node) -> str:
    """Windows come and go while we enumerate, and reading Name on one that has
    just closed raises a COM error. Every name read goes through here."""
    try:
        return (node.Name or "").strip()
    except Exception:
        return ""


def _safe_class(node) -> str:
    try:
        return (node.ClassName or "").strip()
    except Exception:
        return ""


#: A chunk that is nothing but a web address. Reading one aloud is a string of
#: letters and slashes, never a sentence, and on a search results page there is
#: one for every result.
_BARE_URL = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.I)


def _text_of(node, ctype: str) -> str:
    """What this control should contribute, by kind.

    The rule used to be "take whichever of Name and Value is longer", which is
    right for an edit box and wrong for a link: a link's Value is its href, and
    an href is almost always longer than the words it sits under. So every link
    on a page was read as its address. On a search results page that is the
    whole page, which is what "it reads the nav bar" turned out to mean.
    """
    if ctype == "HyperlinkControl":
        # The words, never the destination.
        return _safe_name(node)
    if ctype == "EditControl":
        # Here Value is the typed contents and Name is the field's label.
        value = _safe_value(node)
        return value or _safe_name(node)
    name = _safe_name(node)
    value = _safe_value(node)
    return value if len(value) > len(name) else name


def _is_noise(text: str) -> bool:
    stripped = text.strip().lower()
    if not stripped or stripped in _CHROME_NOISE or len(stripped) < 2:
        return True
    return bool(_BARE_URL.match(stripped))


def _pattern(ctrl, pattern_id: int):
    """Fetch a UI Automation pattern, or None when the control lacks it.

    This library exposes only the generic GetPattern(id); the per-pattern
    availability helpers other UIA bindings offer do not exist here.
    """
    try:
        return ctrl.GetPattern(pattern_id)
    except Exception:
        return None


def _text_pattern_text(ctrl) -> str:
    """Whole-region text via TextPattern. Complete and fast where supported."""
    try:
        pattern = _pattern(ctrl, _auto().PatternId.TextPattern)
        if pattern is None:
            return ""
        rng = pattern.DocumentRange
        if rng is None:
            return ""
        return (rng.GetText(-1) or "").strip()
    except Exception:
        return ""


def _safe_value(node) -> str:
    """Text of an editable control. Kept separate from the name lookup so a
    failure here never discards a name we already read."""
    try:
        pattern = _pattern(node, _auto().PatternId.ValuePattern)
        if pattern is None:
            return ""
        return (pattern.Value or "").strip()
    except Exception:
        return ""


def _collect(root, deadline: float) -> str:
    """Gather text from the accessible descendants of root.

    Chrome renders pages as a tree of named Text and Link nodes rather than one
    TextPattern region, so this walk is what actually reads a web page.
    """
    parts: list[tuple[str, str]] = []   # (control type, text)
    seen: set[str] = set()
    nodes = 0

    def visit(node, depth: int) -> None:
        nonlocal nodes
        if depth > _MAX_DEPTH or nodes > _MAX_NODES or time.monotonic() > deadline:
            return
        nodes += 1
        try:
            ctype = node.ControlTypeName
        except Exception:
            return
        if ctype in _SKIP_CONTROLS:
            return
        if ctype in _TEXT_CONTROLS:
            chunk = _text_of(node, ctype)
            # Headings and nav labels repeat across a page; keep the first.
            key = chunk.lower()
            if chunk and not _is_noise(chunk) and key not in seen:
                seen.add(key)
                parts.append((ctype, chunk))
        try:
            children = node.GetChildren()
        except Exception:
            return
        for child in children:
            visit(child, depth + 1)

    visit(root, 0)
    return "\n".join(_drop_navigation(parts))


#: A link this short with no sentence in it is a menu item, not prose.
_NAV_LINK_CHARS = 45


def _drop_navigation(parts: list) -> list:
    """Remove menu links, but only from a window that has real prose in it.

    "Skip to main content", "Images", "More", "Sign in" are links in the page
    rather than browser furniture, so the furniture list cannot catch them and
    neither can anything that only looks at one node. What marks them is the
    company they keep: a short link with no sentence in it, sitting in a window
    that also contains paragraphs.

    The guard matters. On a page that is genuinely a list of links — a search
    results page read on purpose, a bookmarks manager — dropping them would
    leave nothing at all, so this only fires when prose exists to keep.
    """
    prose = sum(1 for ctype, text in parts
                if ctype != "HyperlinkControl" and len(text) > 80)
    if prose < 3:
        return [text for _ctype, text in parts]
    kept = []
    for ctype, text in parts:
        short_link = (ctype == "HyperlinkControl"
                      and len(text) <= _NAV_LINK_CHARS
                      and not any(c in text for c in ".!?"))
        if not short_link:
            kept.append(text)
    return kept


def _candidate_roots(win, deadline: float) -> list:
    """Document regions inside the window, best first.

    Scoping to a document keeps browser and shell chrome out of the reading.
    """
    found = []
    queue = [(win, 0)]
    nodes = 0
    while queue and time.monotonic() < deadline and nodes < 1500:
        node, depth = queue.pop(0)
        nodes += 1
        if depth > _MAX_DEPTH:
            continue
        try:
            if node.ControlTypeName == "DocumentControl":
                found.append(node)
                if len(found) >= 6:
                    break
            children = node.GetChildren()
        except Exception:
            continue
        queue.extend((c, depth + 1) for c in children)
    return found


def _prose_score(text: str) -> int:
    """Characters that are part of a sentence, not a label.

    Regions used to be ranked by total length, which picks a sidebar over a
    conversation whenever the sidebar has enough items: fifty menu entries
    outweigh three paragraphs. Counting only lines long enough to be prose
    ranks by what someone actually wants read, and a window with no prose at
    all still scores zero everywhere and falls through to the old behaviour.
    """
    return sum(len(line) for line in text.splitlines() if len(line.strip()) > 80)


def _extract(win, deadline: float) -> str:
    """Best available text for a window, trying the cheapest route first."""
    direct = _text_pattern_text(win)
    if len(direct) >= _MIN_USEFUL:
        return direct

    best = ""
    best_score = -1
    for doc in _candidate_roots(win, deadline):
        if time.monotonic() > deadline:
            break
        text = _text_pattern_text(doc) or _collect(doc, deadline)
        score = _prose_score(text)
        if score > best_score:
            best, best_score = text, score
    if len(best) >= _MIN_USEFUL:
        return best

    whole = _collect(win, deadline)
    return whole if len(whole) > len(best) else best


def _browser_url(win) -> str:
    """Chrome and Edge expose the address bar as a named Edit control."""
    for name in ("Address and search bar", "Address field",
                 "Search or enter address"):
        try:
            bar = win.EditControl(Name=name, searchDepth=12)
            if bar.Exists(maxSearchSeconds=0.3):
                value = (bar.GetValuePattern().Value or "").strip()
                if value:
                    return value
        except Exception:
            continue
    return ""


def window_info() -> WindowInfo:
    auto = _auto()
    with auto.UIAutomationInitializerInThread(debug=False):
        try:
            win = auto.GetForegroundControl()
            if win is None:
                return WindowInfo()
            try:
                app = (win.ClassName or "").strip()
            except Exception:
                app = ""
            return WindowInfo(title=_safe_name(win),
                              url=_browser_url(win), app=app)
        except Exception:
            return WindowInfo()


def capture(timeout: float = _TIME_BUDGET) -> Document | None:
    """Read the focused window. None when nothing readable is exposed."""
    auto = _auto()
    deadline = time.monotonic() + timeout
    with auto.UIAutomationInitializerInThread(debug=False):
        win = auto.GetForegroundControl()
        if win is None:
            return None
        title = _safe_name(win)
        klass = _safe_class(win)
        url = _browser_url(win)
        text = _extract(win, deadline).strip()

    if len(text) < _MIN_USEFUL:
        return None
    return Document(text=text, title=title or "Window", uri=url,
                    source="uia", meta={"url": url, "window": title, "class": klass})


def capture_window(match: str, timeout: float = _TIME_BUDGET) -> Document | None:
    """Read a named top-level window instead of the focused one."""
    auto = _auto()
    deadline = time.monotonic() + timeout
    with auto.UIAutomationInitializerInThread(debug=False):
        root = auto.GetRootControl()
        win = next((w for w in root.GetChildren()
                    if match.lower() in _safe_name(w).lower()), None)
        if win is None:
            return None
        title = _safe_name(win)
        url = _browser_url(win)
        text = _extract(win, deadline).strip()
    if len(text) < _MIN_USEFUL:
        return None
    return Document(text=text, title=title, uri=url, source="uia",
                    meta={"url": url, "window": title})


def capture_selection() -> Document | None:
    """Read only the highlighted text, without touching the clipboard."""
    auto = _auto()
    with auto.UIAutomationInitializerInThread(debug=False):
        try:
            focused = auto.GetFocusedControl()
            if focused is None:
                return None
            pattern = _pattern(focused, auto.PatternId.TextPattern)
            if pattern is None:
                return None
            ranges = pattern.GetSelection()
            if not ranges:
                return None
            chunks = []
            for i in range(ranges.Length):
                try:
                    chunks.append(ranges.GetElement(i).GetText(-1) or "")
                except Exception:
                    continue
            text = "\n".join(c for c in chunks if c).strip()
        except Exception:
            return None
    if len(text) < 2:
        return None
    return Document(text=text, title="Selection", source="selection")


def chrome_accessibility_state(timeout: float = 6.0) -> str:
    """Whether Chrome is exposing page content: on, off, unknown, no-chrome.

    The question is whether any page text reaches us at all, not whether there
    is a lot of it. _collect already discards browser furniture, so anything it
    returns beyond the usual readable minimum is page content. An earlier
    version demanded 120 characters, which reported "off" for a short page and
    told the user to change a Chrome flag they had already set.

    "unknown" matters for the same reason. Running out of the search budget is
    not evidence of anything, and reporting it as "off" would send someone to
    fix a setting that was never the problem.
    """
    auto = _auto()
    deadline = time.monotonic() + timeout
    ran_out = False
    with auto.UIAutomationInitializerInThread(debug=False):
        root = auto.GetRootControl()
        windows = [w for w in root.GetChildren()
                   if _safe_class(w) == "Chrome_WidgetWin_1" and _safe_name(w)]
        if not windows:
            return "no-chrome"
        for win in windows[:8]:
            if time.monotonic() > deadline:
                ran_out = True
                break
            for doc in _candidate_roots(win, deadline):
                if len(_collect(doc, deadline)) >= _MIN_USEFUL:
                    return "on"
        if time.monotonic() > deadline:
            ran_out = True
    return "unknown" if ran_out else "off"
