# Type Before Answer v3 - Enhanced (unofficial fork)
# Based on "Type Before Answer v3" by Jack Thompson (MIT license).
# Modified 2026-11-17; this fork is distributed under the GNU AGPL v3 or later.
# See LICENSE (AGPL3) and LICENSE-MIT (original MIT notice).
"""Optional reviewer typing box with review-friendly focus handling."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import aqt
from aqt import gui_hooks, mw
from aqt.qt import QAction, QMenu
from aqt.reviewer import Reviewer
from aqt.utils import tooltip

logger = logging.getLogger(__name__)

ADDON_LABEL = "Type Before Answer"
INPUT_ID = "type-before-answer-v3-input"
WRAPPER_ID = "type-before-answer-v3-wrapper"
CONTAINER_ID = "type-before-answer-v3-container"
COMMAND_PREFIX = "type_before_answer_v3:"
FOCUS_COMMAND = f"{COMMAND_PREFIX}focus_reviewer"
RESTORE_COMMAND = f"{COMMAND_PREFIX}restore_answer_ui"
SIZE_COMMAND_PREFIX = f"{COMMAND_PREFIX}box_size:"
TEXT_COMMAND_PREFIX = f"{COMMAND_PREFIX}text:"
HTML_COMMAND_PREFIX = f"{COMMAND_PREFIX}html:"
CORRECTIONS_COMMAND_PREFIX = f"{COMMAND_PREFIX}corrections:"
FOCUS_DELAY_MS = 50
TEXT_REPORT_DELAY_MS = 250
RED_COLOR = "#ef4444"

DEFAULT_FONT_SIZE_PT = 12
DEFAULT_FOCUS_TEXT_BOX = False
DEFAULT_FONT_FAMILY = (
    '"Malgun Gothic", "Apple SD Gothic Neo", "Hiragino Sans", "MS Gothic", sans-serif'
)
DEFAULT_BOX_HEIGHT_PX = 96
MIN_FONT_SIZE_PT = 6
MAX_FONT_SIZE_PT = 72
MIN_BOX_WIDTH_PX = 160
MAX_BOX_WIDTH_PX = 2400
MIN_BOX_HEIGHT_PX = 48
MAX_BOX_HEIGHT_PX = 1600
MAX_TEXT_LENGTH = 100_000
MAX_HTML_LENGTH = MAX_TEXT_LENGTH * 4

STATE_FILE = Path(__file__).with_name("user_files") / "state.json"
LEGACY_STATE_FILES = (
    Path(__file__).resolve().parent.parent
    / "type-before-answer-v2-2"
    / "user_files"
    / "state.json",
    Path(__file__).resolve().parent.parent
    / "type-before-answer_v2_2"
    / "user_files"
    / "state.json",
)

_TEXT_ATTRIBUTE = "_type_before_answer_v3_text"
_HTML_ATTRIBUTE = "_type_before_answer_v3_html"
_CORRECTIONS_ATTRIBUTE = "_type_before_answer_v3_corrections"
_SESSION_ATTRIBUTE = "_type_before_answer_v3_session"
_CARD_ATTRIBUTE = "_type_before_answer_v3_card"
_SIDE_ATTRIBUTE = "_type_before_answer_v3_side"
_ORIGINAL_SHOW_ANSWER_ATTRIBUTE = "_type_before_answer_v3_original_show_answer"
_HOOK_REGISTRY_ATTRIBUTE = "_type_before_answer_v3_hooks"

_type_menu: Optional[QMenu] = None
_toggle_action: Optional[QAction] = None
_focus_action: Optional[QAction] = None


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("Could not read Type Before Answer state file")
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_file(path: Path, data: dict[str, Any]) -> bool:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, indent=2, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(path)
        return True
    except Exception:
        logger.exception("Could not write Type Before Answer state file")
        try:
            temporary_path.unlink(missing_ok=True)
        except Exception:
            logger.debug("Could not remove temporary state file", exc_info=True)
        return False


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(minimum, min(maximum, number))


def _optional_clamped_int(value: Any, minimum: int, maximum: int) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return max(minimum, min(maximum, number))


def _get_config() -> dict[str, Any]:
    config = mw.addonManager.getConfig(__name__) if mw else None
    return config if isinstance(config, dict) else {}


def _font_size_pt() -> int:
    config = _get_config()
    return _clamp_int(
        config.get("font_size_pt"),
        DEFAULT_FONT_SIZE_PT,
        MIN_FONT_SIZE_PT,
        MAX_FONT_SIZE_PT,
    )


def _focus_text_box_on_show_question() -> bool:
    config = _get_config()
    value = config.get("focus_text_box")
    return value if isinstance(value, bool) else DEFAULT_FOCUS_TEXT_BOX


def _get_state() -> dict[str, Any]:
    state_exists = STATE_FILE.exists()
    state = _read_json_file(STATE_FILE)

    if not state_exists:
        for legacy_path in LEGACY_STATE_FILES:
            if not legacy_path.is_file():
                continue
            state = _read_json_file(legacy_path)
            if state:
                _write_json_file(STATE_FILE, state)
            break

    if not isinstance(state.get("enabled"), bool):
        state["enabled"] = True
    return state


def _save_state(state: dict[str, Any]) -> bool:
    return _write_json_file(STATE_FILE, state)


def _is_enabled() -> bool:
    return _get_state()["enabled"] is True


def _set_enabled(enabled: bool) -> None:
    state = _get_state()
    state["enabled"] = enabled
    if not _save_state(state):
        tooltip(
            f"{ADDON_LABEL}: could not save the setting",
            parent=mw,
            period=2000,
        )
        return
    _sync_type_menu()
    _refresh_current_review_ui()
    tooltip(f"{ADDON_LABEL}: {'On' if enabled else 'Off'}", parent=mw, period=1200)
    if not enabled:
        _focus_reviewer(_current_reviewer())


def _bottom_web_available(reviewer: Reviewer) -> bool:
    bottom = getattr(reviewer, "bottom", None)
    return bool(bottom and getattr(bottom, "web", None))


def _current_reviewer() -> Optional[Reviewer]:
    reviewer = getattr(aqt.mw, "reviewer", None)
    if reviewer and getattr(reviewer, "card", None) is not None:
        return reviewer
    return None


def _reviewer_from_context(context: Any) -> Optional[Reviewer]:
    reviewer = getattr(context, "reviewer", None)
    if isinstance(reviewer, Reviewer) and reviewer is _current_reviewer():
        return reviewer
    return None


def _reviewer_text(reviewer: Optional[Reviewer]) -> str:
    if not reviewer:
        return ""
    text = getattr(reviewer, _TEXT_ATTRIBUTE, "")
    return text if isinstance(text, str) else ""


def _set_reviewer_text(reviewer: Optional[Reviewer], text: str) -> None:
    if reviewer:
        setattr(reviewer, _TEXT_ATTRIBUTE, text[:MAX_TEXT_LENGTH])


def _reviewer_html(reviewer: Optional[Reviewer]) -> str:
    if not reviewer:
        return ""
    html = getattr(reviewer, _HTML_ATTRIBUTE, "")
    return html if isinstance(html, str) else ""


def _set_reviewer_html(reviewer: Optional[Reviewer], html: str) -> None:
    if reviewer and len(html) <= MAX_HTML_LENGTH:
        setattr(reviewer, _HTML_ATTRIBUTE, html)


def _reviewer_corrections(reviewer: Optional[Reviewer]) -> str:
    if not reviewer:
        return ""
    html = getattr(reviewer, _CORRECTIONS_ATTRIBUTE, "")
    return html if isinstance(html, str) else ""


def _set_reviewer_corrections(reviewer: Optional[Reviewer], html: str) -> None:
    if reviewer and len(html) <= MAX_HTML_LENGTH:
        setattr(reviewer, _CORRECTIONS_ATTRIBUTE, html)


def _reviewer_session(reviewer: Reviewer) -> int:
    session = getattr(reviewer, _SESSION_ATTRIBUTE, 0)
    return session if isinstance(session, int) and not isinstance(session, bool) else 0


def _advance_reviewer_session(reviewer: Reviewer) -> int:
    session = _reviewer_session(reviewer) + 1
    setattr(reviewer, _SESSION_ATTRIBUTE, session)
    return session


def _message_for_current_session(
    message: str, prefix: str, reviewer: Optional[Reviewer]
) -> Optional[str]:
    if not reviewer or not message.startswith(prefix):
        return None
    payload = message[len(prefix) :]
    raw_session, separator, value = payload.partition(":")
    if (
        not separator
        or not raw_session
        or len(raw_session) > 20
        or not raw_session.isascii()
        or not raw_session.isdecimal()
    ):
        return None
    try:
        session = int(raw_session)
    except ValueError:
        return None
    return value if session == _reviewer_session(reviewer) else None


def _box_dimensions_from_state(
    state: Optional[dict[str, Any]] = None,
) -> tuple[Optional[int], int]:
    state = state if state is not None else _get_state()
    width = _optional_clamped_int(state.get("box_width_px"), MIN_BOX_WIDTH_PX, MAX_BOX_WIDTH_PX)
    height = _clamp_int(
        state.get("box_height_px"),
        DEFAULT_BOX_HEIGHT_PX,
        MIN_BOX_HEIGHT_PX,
        MAX_BOX_HEIGHT_PX,
    )
    return width, height


def _build_review_ui_js(reviewer: Reviewer, show_input: bool, auto_focus: bool) -> str:
    state = _get_state()
    width_px, height_px = _box_dimensions_from_state(state)
    enabled = state["enabled"] is True
    session = _reviewer_session(reviewer)
    answer_side = getattr(reviewer, "state", None) == "answer"
    options = {
        "answerSide": answer_side,
        "autoFocus": auto_focus,
        "boxId": f"{INPUT_ID}-box",
        "containerId": CONTAINER_ID,
        "correctionsCommandPrefix": f"{CORRECTIONS_COMMAND_PREFIX}{session}:",
        "correctionsValue": _reviewer_corrections(reviewer),
        "enabled": enabled,
        "focusCommand": f"{FOCUS_COMMAND}:{session}:",
        "focusDelayMs": FOCUS_DELAY_MS,
        "fontFamily": DEFAULT_FONT_FAMILY,
        "fontSizePt": _font_size_pt(),
        "heightPx": height_px,
        "htmlCommandPrefix": f"{HTML_COMMAND_PREFIX}{session}:",
        "htmlValue": _reviewer_html(reviewer),
        "inputId": INPUT_ID,
        "maxHeightPx": MAX_BOX_HEIGHT_PX,
        "maxLength": MAX_TEXT_LENGTH,
        "maxWidthPx": MAX_BOX_WIDTH_PX,
        "minHeightPx": MIN_BOX_HEIGHT_PX,
        "minWidthPx": MIN_BOX_WIDTH_PX,
        "redColor": RED_COLOR,
        "restoreCommand": f"{RESTORE_COMMAND}:{session}:",
        "session": session,
        "showInput": enabled and show_input,
        "sizeCommandPrefix": f"{SIZE_COMMAND_PREFIX}{session}:",
        "textCommandPrefix": f"{TEXT_COMMAND_PREFIX}{session}:",
        "textReportDelayMs": TEXT_REPORT_DELAY_MS,
        "textValue": _reviewer_text(reviewer),
        "widthPx": width_px,
        "wrapperId": WRAPPER_ID,
    }
    return f"""
(function() {{
    const options = {json.dumps(options)};
    const answerObserverKey = "typeBeforeAnswerV3AnswerObserver";
    const strikeClass = "type-before-answer-v3-strike";
    const lineClass = "type-before-answer-v3-line";
    const lineTextClass = "type-before-answer-v3-line-text";
    const lineInputClass = "type-before-answer-v3-line-input";

    function stopAnswerObserver() {{
        const observer = window[answerObserverKey];
        if (observer) {{
            observer.disconnect();
            window[answerObserverKey] = null;
        }}
    }}

    function escapeHtml(text) {{
        return String(text)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }}

    function elementOf(target) {{
        if (!target) {{
            return null;
        }}
        return target.nodeType === 1 ? target : target.parentElement;
    }}

    function closestWithin(target, selector) {{
        const element = elementOf(target);
        if (!element || !element.closest) {{
            return null;
        }}
        return element.closest(selector);
    }}

    function linesRoot() {{
        return document.getElementById(options.inputId);
    }}

    function lineRows(root) {{
        const base = root || linesRoot();
        if (!base) {{
            return [];
        }}
        return Array.prototype.slice.call(base.querySelectorAll("." + lineClass));
    }}

    function lineTexts(root) {{
        const base = root || linesRoot();
        if (!base) {{
            return [];
        }}
        return Array.prototype.slice.call(base.querySelectorAll("." + lineTextClass));
    }}

    function lineInputs(root) {{
        const base = root || linesRoot();
        if (!base) {{
            return [];
        }}
        return Array.prototype.slice.call(base.querySelectorAll("." + lineInputClass));
    }}

    function trailingInput(row) {{
        if (!row) {{
            return null;
        }}
        const inputs = row.querySelectorAll("." + lineInputClass);
        return inputs.length ? inputs[inputs.length - 1] : null;
    }}

    function lastTrailingInput() {{
        const rows = lineRows();
        if (!rows.length) {{
            return null;
        }}
        return trailingInput(rows[rows.length - 1]);
    }}

    function splitLines(text) {{
        return String(text == null ? "" : text).split(/\\r\\n|\\r|\\n/);
    }}

    function parseStoredArray(raw) {{
        if (!raw) {{
            return null;
        }}
        try {{
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {{
                return parsed;
            }}
        }} catch (error) {{
            return null;
        }}
        return null;
    }}

    function ensureCorrectionsStyle() {{
        const styleId = options.inputId + "-corrections-style";
        let styleElement = document.getElementById(styleId);
        if (!options.answerSide) {{
            if (styleElement) {{
                styleElement.remove();
            }}
            return;
        }}
        if (!styleElement) {{
            styleElement = document.createElement("style");
            styleElement.id = styleId;
            document.head.appendChild(styleElement);
        }}
        styleElement.textContent =
            "." + lineInputClass + ", ." + lineInputClass + " * {{ color: "
            + options.redColor + " !important;"
            + " -webkit-user-select: text !important;"
            + " user-select: text !important; }}";
    }}

    function selectionTouchesOriginal() {{
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) {{
            return false;
        }}
        const range = selection.getRangeAt(0);
        const texts = lineTexts();
        for (let index = 0; index < texts.length; index += 1) {{
            const textElement = texts[index];
            if (range.collapsed) {{
                if (textElement.contains(range.startContainer)) {{
                    return true;
                }}
            }} else if (range.intersectsNode(textElement)) {{
                return true;
            }}
        }}
        return false;
    }}

    function clampRangeToInput(inputElement, range) {{
        const bounds = document.createRange();
        bounds.selectNodeContents(inputElement);
        const clamped = range.cloneRange();
        try {{
            if (clamped.compareBoundaryPoints(Range.START_TO_START, bounds) < 0) {{
                clamped.setStart(bounds.startContainer, bounds.startOffset);
            }}
            if (clamped.compareBoundaryPoints(Range.END_TO_END, bounds) > 0) {{
                clamped.setEnd(bounds.endContainer, bounds.endOffset);
            }}
        }} catch (error) {{
            return null;
        }}
        return clamped;
    }}

    function caretOffsetInInput(inputElement, range) {{
        const before = document.createRange();
        before.selectNodeContents(inputElement);
        try {{
            before.setEnd(range.startContainer, range.startOffset);
        }} catch (error) {{
            return null;
        }}
        return before.toString().length;
    }}

    function positionAtOffset(inputElement, offset) {{
        const walker = document.createTreeWalker(inputElement, NodeFilter.SHOW_TEXT);
        let remaining = offset;
        let last = null;
        let node = walker.nextNode();
        while (node) {{
            const length = node.nodeValue ? node.nodeValue.length : 0;
            if (remaining <= length) {{
                return {{ node: node, offset: remaining }};
            }}
            remaining -= length;
            last = node;
            node = walker.nextNode();
        }}
        if (last) {{
            return {{ node: last, offset: last.nodeValue.length }};
        }}
        return {{ node: inputElement, offset: 0 }};
    }}

    function graphemeLength(text, fromEnd) {{
        if (!text) {{
            return 0;
        }}
        if (window.Intl && typeof Intl.Segmenter === "function") {{
            try {{
                const segmenter = new Intl.Segmenter(
                    undefined, {{ granularity: "grapheme" }}
                );
                const segments = Array.from(segmenter.segment(text));
                if (segments.length) {{
                    return segments[
                        fromEnd ? segments.length - 1 : 0
                    ].segment.length;
                }}
            }} catch (error) {{
                // Fall through to the code point fallback.
            }}
        }}
        const points = Array.from(text);
        return points.length
            ? points[fromEnd ? points.length - 1 : 0].length
            : 0;
    }}

    function deleteInsideInput(inputElement, forward) {{
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || !selection.isCollapsed) {{
            return false;
        }}
        const range = selection.getRangeAt(0);
        if (!inputElement.contains(range.startContainer)) {{
            return false;
        }}
        const content = inputElement.textContent || "";
        const caret = caretOffsetInInput(inputElement, range);
        if (caret === null) {{
            return false;
        }}
        let from = 0;
        let to = 0;
        if (forward) {{
            if (caret >= content.length) {{
                return false;
            }}
            from = caret;
            to = caret + graphemeLength(content.slice(caret), false);
        }} else {{
            if (caret <= 0) {{
                return false;
            }}
            from = caret - graphemeLength(content.slice(0, caret), true);
            to = caret;
        }}
        const start = positionAtOffset(inputElement, from);
        const end = positionAtOffset(inputElement, to);
        const target = document.createRange();
        try {{
            target.setStart(start.node, start.offset);
            target.setEnd(end.node, end.offset);
        }} catch (error) {{
            return false;
        }}
        selection.removeAllRanges();
        selection.addRange(target);
        let removed = false;
        try {{
            removed = document.execCommand("delete", false, null);
        }} catch (error) {{
            removed = false;
        }}
        if (!removed) {{
            target.deleteContents();
        }}
        const restored = positionAtOffset(inputElement, from);
        const collapsed = document.createRange();
        collapsed.setStart(restored.node, restored.offset);
        collapsed.collapse(true);
        selection.removeAllRanges();
        selection.addRange(collapsed);
        return true;
    }}

    function collectTextCharacters(element) {{
        const chars = [];
        const struck = [];
        const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node) {{
            const isStruck = !!closestWithin(node, "." + strikeClass);
            const text = node.nodeValue || "";
            for (let index = 0; index < text.length; index += 1) {{
                chars.push(text.charAt(index));
                struck.push(isStruck);
            }}
            node = walker.nextNode();
        }}
        return {{ chars: chars, struck: struck }};
    }}

    function offsetWithinElement(element, container, offset) {{
        const root = document.createRange();
        root.selectNodeContents(element);
        const probe = document.createRange();
        try {{
            probe.setStart(container, offset);
            probe.collapse(true);
        }} catch (error) {{
            return null;
        }}
        if (probe.compareBoundaryPoints(Range.START_TO_START, root) <= 0) {{
            return 0;
        }}
        if (probe.compareBoundaryPoints(Range.END_TO_END, root) >= 0) {{
            return root.toString().length;
        }}
        const measure = document.createRange();
        measure.selectNodeContents(element);
        try {{
            measure.setEnd(container, offset);
        }} catch (error) {{
            return null;
        }}
        return measure.toString().length;
    }}

    function rebuildTextElement(element, chars, struck) {{
        const fragment = document.createDocumentFragment();
        let index = 0;
        while (index < chars.length) {{
            const isStruck = struck[index];
            let end = index + 1;
            while (end < chars.length && struck[end] === isStruck) {{
                end += 1;
            }}
            const text = chars.slice(index, end).join("");
            if (isStruck) {{
                const span = document.createElement("span");
                span.className = strikeClass;
                span.style.textDecorationLine = "line-through";
                span.style.textDecorationColor = options.redColor;
                span.textContent = text;
                fragment.appendChild(span);
            }} else {{
                fragment.appendChild(document.createTextNode(text));
            }}
            index = end;
        }}
        element.innerHTML = "";
        element.appendChild(fragment);
    }}

    function focusInputAtEnd(inputElement) {{
        if (!inputElement) {{
            return;
        }}
        inputElement.focus({{ preventScroll: true }});
        const range = document.createRange();
        range.selectNodeContents(inputElement);
        range.collapse(false);
        const selection = window.getSelection();
        if (!selection) {{
            return;
        }}
        selection.removeAllRanges();
        selection.addRange(range);
    }}

    function caretRectInInput(inputElement) {{
        const selection = window.getSelection();
        if (!selection || !selection.isCollapsed || selection.rangeCount === 0) {{
            return null;
        }}
        const range = selection.getRangeAt(0);
        if (!inputElement.contains(range.startContainer)) {{
            return null;
        }}
        const content = inputElement.textContent || "";
        const caret = caretOffsetInInput(inputElement, range);
        if (caret === null) {{
            return null;
        }}
        if (!content.length) {{
            return inputElement.getBoundingClientRect();
        }}
        const from = caret > 0 ? caret - 1 : caret;
        const to = caret > 0 ? caret : caret + 1;
        const start = positionAtOffset(inputElement, from);
        const end = positionAtOffset(inputElement, to);
        const probe = document.createRange();
        try {{
            probe.setStart(start.node, start.offset);
            probe.setEnd(end.node, end.offset);
        }} catch (error) {{
            return null;
        }}
        const rects = probe.getClientRects();
        if (rects && rects.length) {{
            return rects[rects.length - 1];
        }}
        const rect = probe.getBoundingClientRect();
        if (rect && (rect.width || rect.height)) {{
            return rect;
        }}
        return null;
    }}

    function lineBoxes(element) {{
        const rects = element.getClientRects();
        const boxes = [];
        for (let index = 0; index < rects.length; index += 1) {{
            boxes.push(rects[index]);
        }}
        return boxes;
    }}

    function caretOnEdgeLine(element, atTop) {{
        const boxes = lineBoxes(element);
        const caret = caretRectInInput(element);
        if (!boxes.length || !caret) {{
            return true;
        }}
        let edge = atTop ? boxes[0].top : boxes[0].bottom;
        for (let index = 1; index < boxes.length; index += 1) {{
            edge = atTop
                ? Math.min(edge, boxes[index].top)
                : Math.max(edge, boxes[index].bottom);
        }}
        return atTop ? caret.top <= edge + 2 : caret.bottom >= edge - 2;
    }}

    function scrollRowIntoView(element) {{
        const row = element.closest("." + lineClass);
        if (row && typeof row.scrollIntoView === "function") {{
            row.scrollIntoView({{ block: "nearest", inline: "nearest" }});
        }}
    }}

    function firstVisibleLineBox(element) {{
        const boxes = lineBoxes(element);
        if (!boxes.length) {{
            return element.getBoundingClientRect();
        }}
        const container = box.getBoundingClientRect();
        for (let index = 0; index < boxes.length; index += 1) {{
            const candidate = boxes[index];
            if (
                candidate.bottom > container.top + 1
                && candidate.top < container.bottom - 1
            ) {{
                return candidate;
            }}
        }}
        return boxes[0];
    }}

    function focusInputAtColumn(inputElement, x) {{
        if (!inputElement) {{
            return;
        }}
        scrollRowIntoView(inputElement);
        const first = firstVisibleLineBox(inputElement);
        inputElement.focus({{ preventScroll: true }});
        const selection = window.getSelection();
        let placed = false;
        if (selection && typeof document.caretRangeFromPoint === "function") {{
            const y = (first.top + first.bottom) / 2;
            const clampedX = Math.min(
                Math.max(x, first.left + 1), first.right - 1
            );
            const candidates = [x, clampedX];
            for (let index = 0; index < candidates.length; index += 1) {{
                const pointRange = document.caretRangeFromPoint(
                    candidates[index], y
                );
                if (
                    pointRange
                    && inputElement.contains(pointRange.startContainer)
                ) {{
                    selection.removeAllRanges();
                    selection.addRange(pointRange);
                    placed = true;
                    break;
                }}
            }}
        }}
        if (!placed) {{
            focusInputAtEnd(inputElement);
        }}
        scrollRowIntoView(inputElement);
    }}

    function moveToAdjacentLine(inputElement, direction) {{
        const row = inputElement.closest("." + lineClass);
        const rows = lineRows();
        if (!row || !rows.length) {{
            return false;
        }}
        const current = rows.indexOf(row);
        if (current < 0) {{
            return false;
        }}
        const target = rows[current + direction];
        if (!target) {{
            return false;
        }}
        const targetInput = trailingInput(target);
        if (!targetInput) {{
            return false;
        }}
        const caret = caretRectInInput(inputElement);
        focusInputAtColumn(targetInput, caret ? caret.right : 0);
        return true;
    }}

    function caretOffsetInRow(row, gap, range) {{
        let offset = 0;
        const children = Array.prototype.slice.call(row.children);
        for (let index = 0; index < children.length; index += 1) {{
            const child = children[index];
            if (child === gap) {{
                const inside = caretOffsetInInput(gap, range);
                return inside === null ? null : offset + inside;
            }}
            offset += (child.textContent || "").length;
        }}
        return null;
    }}

    function offsetWithinRow(row, container, offset) {{
        if (!row) {{
            return null;
        }}
        const children = Array.prototype.slice.call(row.children);
        let accumulated = 0;
        for (let index = 0; index < children.length; index += 1) {{
            const child = children[index];
            if (child === container || child.contains(container)) {{
                const inside = offsetWithinElement(child, container, offset);
                return accumulated + (inside === null ? 0 : inside);
            }}
            accumulated += (child.textContent || "").length;
        }}
        if (container === row) {{
            let total = 0;
            const limit = Math.min(offset, children.length);
            for (let index = 0; index < limit; index += 1) {{
                total += (children[index].textContent || "").length;
            }}
            return total;
        }}
        return null;
    }}

    function stepRowOffset(text, offset, direction) {{
        const value = String(text == null ? "" : text);
        if (direction < 0) {{
            if (offset <= 0) {{
                return -1;
            }}
            return offset - graphemeLength(value.slice(0, offset), true);
        }}
        if (offset >= value.length) {{
            return -1;
        }}
        return offset + graphemeLength(value.slice(offset), false);
    }}

    function focusGapAtOffset(gap, offset) {{
        if (!gap) {{
            return false;
        }}
        const length = (gap.textContent || "").length;
        const clamped = Math.max(0, Math.min(offset, length));
        scrollRowIntoView(gap);
        gap.focus({{ preventScroll: true }});
        const position = positionAtOffset(gap, clamped);
        const range = document.createRange();
        try {{
            range.setStart(position.node, position.offset);
            range.collapse(true);
        }} catch (error) {{
            return false;
        }}
        const selection = window.getSelection();
        if (!selection) {{
            return false;
        }}
        selection.removeAllRanges();
        selection.addRange(range);
        scrollRowIntoView(gap);
        return true;
    }}

    function splitTextAtOffset(textElement, offset) {{
        const row = closestWithin(textElement, "." + lineClass);
        if (!row) {{
            return null;
        }}
        const parent = textElement.parentElement;
        if (!parent) {{
            return null;
        }}
        const length = (textElement.textContent || "").length;
        const clamped = Math.max(0, Math.min(offset, length));
        const position = positionAtOffset(textElement, clamped);
        const lineIndex = Number(row.dataset.typeBeforeAnswerV3Line || 0);
        const beforeRange = document.createRange();
        beforeRange.selectNodeContents(textElement);
        const afterRange = document.createRange();
        afterRange.selectNodeContents(textElement);
        try {{
            beforeRange.setEnd(position.node, position.offset);
            afterRange.setStart(position.node, position.offset);
        }} catch (error) {{
            return null;
        }}
        const beforeSpan = makeTextSpan(lineIndex, beforeRange.cloneContents());
        const afterSpan = makeTextSpan(lineIndex, afterRange.cloneContents());
        const gap = createGapElement(lineIndex);
        parent.insertBefore(beforeSpan, textElement);
        parent.insertBefore(gap, textElement);
        parent.insertBefore(afterSpan, textElement);
        parent.removeChild(textElement);
        if (!beforeSpan.textContent) {{
            beforeSpan.remove();
        }}
        if (!afterSpan.textContent) {{
            afterSpan.remove();
        }}
        scrollRowIntoView(gap);
        gap.focus({{ preventScroll: true }});
        const selection = window.getSelection();
        if (selection) {{
            const range = document.createRange();
            range.setStart(gap, 0);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
        }}
        return gap;
    }}

    function pruneEmptyGaps(row) {{
        if (!row) {{
            return false;
        }}
        let removed = false;
        Array.prototype.slice.call(
            row.querySelectorAll("." + lineInputClass)
        ).forEach(function(gap) {{
            if (removeGapIfEmpty(gap)) {{
                removed = true;
            }}
        }});
        if (removed) {{
            reportStateSoon();
        }}
        return removed;
    }}

    function placeCaretAtRowOffset(row, targetOffset) {{
        if (!row) {{
            return false;
        }}
        pruneEmptyGaps(row);
        const children = Array.prototype.slice.call(row.children);
        let accumulated = 0;
        for (let index = 0; index < children.length; index += 1) {{
            const child = children[index];
            const length = (child.textContent || "").length;
            const isGap = child.classList.contains(lineInputClass);
            if (
                targetOffset < accumulated + length
                || (isGap && targetOffset <= accumulated + length)
            ) {{
                if (isGap) {{
                    return focusGapAtOffset(child, targetOffset - accumulated);
                }}
                return !!splitTextAtOffset(child, targetOffset - accumulated);
            }}
            accumulated += length;
        }}
        const last = row.lastElementChild;
        if (last && last.classList.contains(lineInputClass)) {{
            return focusGapAtOffset(last, (last.textContent || "").length);
        }}
        return false;
    }}

    function moveCaretHorizontally(lineInput, direction, byWord) {{
        const selection = window.getSelection();
        if (!selection || !selection.isCollapsed || selection.rangeCount === 0) {{
            return false;
        }}
        const row = closestWithin(lineInput, "." + lineClass);
        if (!row) {{
            return false;
        }}
        const range = selection.getRangeAt(0);
        const insideGap = caretOffsetInInput(lineInput, range);
        if (insideGap === null) {{
            return false;
        }}
        const gapLength = (lineInput.textContent || "").length;
        if (!byWord) {{
            if (direction < 0 && insideGap > 0) {{
                return false;
            }}
            if (direction > 0 && insideGap < gapLength) {{
                return false;
            }}
        }}
        const rowOffset = caretOffsetInRow(row, lineInput, range);
        if (rowOffset === null) {{
            return false;
        }}
        const rowText = row.textContent || "";
        const target = byWord
            ? wordBoundaryOffset(rowText, rowOffset, direction)
            : stepRowOffset(rowText, rowOffset, direction);
        if (target < 0) {{
            const rows = lineRows();
            const current = rows.indexOf(row);
            const adjacent = rows[current + direction];
            if (!adjacent) {{
                return false;
            }}
            return placeCaretAtRowOffset(
                adjacent,
                direction < 0 ? (adjacent.textContent || "").length : 0
            );
        }}
        return placeCaretAtRowOffset(row, target);
    }}

    function nearestLineBox(element, y) {{
        const boxes = lineBoxes(element);
        let best = null;
        for (let index = 0; index < boxes.length; index += 1) {{
            const candidate = boxes[index];
            if (y >= candidate.top && y <= candidate.bottom) {{
                return candidate;
            }}
            if (!best) {{
                best = candidate;
                continue;
            }}
            const bestDistance = Math.abs(y - (best.top + best.bottom) / 2);
            const candidateDistance = Math.abs(
                y - (candidate.top + candidate.bottom) / 2
            );
            if (candidateDistance < bestDistance) {{
                best = candidate;
            }}
        }}
        return best;
    }}

    function moveCaretVertically(lineInput, direction) {{
        if (typeof document.caretRangeFromPoint !== "function") {{
            return false;
        }}
        const caret = caretRectInInput(lineInput);
        if (!caret) {{
            return false;
        }}
        const height = Math.max(caret.bottom - caret.top, 8);
        const y = direction < 0
            ? caret.top - height / 2
            : caret.bottom + height / 2;
        const point = document.caretRangeFromPoint(caret.right, y);
        if (!point) {{
            return false;
        }}
        const row = closestWithin(point.startContainer, "." + lineClass);
        if (!row) {{
            return false;
        }}
        const host = point.startContainer.nodeType === 1
            ? point.startContainer
            : point.startContainer.parentElement;
        if (!host) {{
            return false;
        }}
        const boxRect = nearestLineBox(host, y);
        if (!boxRect) {{
            return false;
        }}
        if (direction < 0 && boxRect.bottom > caret.top + 2) {{
            return false;
        }}
        if (direction > 0 && boxRect.top < caret.bottom - 2) {{
            return false;
        }}
        const offset = offsetWithinRow(
            row, point.startContainer, point.startOffset
        );
        if (offset === null) {{
            return false;
        }}
        return placeCaretAtRowOffset(row, offset);
    }}

    function wordBoundaryOffset(text, offset, direction) {{
        const value = String(text == null ? "" : text);
        if (!value.length) {{
            return -1;
        }}
        let index = Math.max(0, Math.min(offset, value.length));
        const isSpace = function(character) {{
            return /\\s/.test(character);
        }};
        if (direction < 0) {{
            if (index <= 0) {{
                return -1;
            }}
            while (index > 0 && isSpace(value.charAt(index - 1))) {{
                index -= 1;
            }}
            while (index > 0 && !isSpace(value.charAt(index - 1))) {{
                index -= 1;
            }}
            return index;
        }}
        if (index >= value.length) {{
            return -1;
        }}
        while (index < value.length && isSpace(value.charAt(index))) {{
            index += 1;
        }}
        while (index < value.length && !isSpace(value.charAt(index))) {{
            index += 1;
        }}
        return index;
    }}

    function rectAtDomPosition(node, offset) {{
        if (node && node.nodeType === 3) {{
            const text = node.nodeValue || "";
            if (text.length) {{
                const index = Math.max(0, Math.min(offset, text.length));
                const from = index > 0 ? index - 1 : 0;
                const to = index > 0 ? index : 1;
                const range = document.createRange();
                try {{
                    range.setStart(node, from);
                    range.setEnd(node, to);
                }} catch (error) {{
                    return null;
                }}
                const rect = range.getBoundingClientRect();
                if (rect && (rect.width || rect.height)) {{
                    const x = index > 0 ? rect.right : rect.left;
                    return {{
                        left: x, right: x, top: rect.top, bottom: rect.bottom
                    }};
                }}
            }}
        }}
        const element = node && node.nodeType === 1
            ? node
            : (node ? node.parentElement : null);
        if (!element || typeof element.getBoundingClientRect !== "function") {{
            return null;
        }}
        const boxRect = element.getBoundingClientRect();
        if (!boxRect || (!boxRect.width && !boxRect.height)) {{
            return null;
        }}
        return {{
            left: boxRect.left + 1,
            right: boxRect.right,
            top: boxRect.top,
            bottom: boxRect.bottom
        }};
    }}

    function positionOnEdgeLine(gap, node, offset, atTop) {{
        const boxes = lineBoxes(gap);
        const rect = rectAtDomPosition(node, offset);
        if (!boxes.length || !rect) {{
            return true;
        }}
        let edge = atTop ? boxes[0].top : boxes[0].bottom;
        for (let index = 1; index < boxes.length; index += 1) {{
            edge = atTop
                ? Math.min(edge, boxes[index].top)
                : Math.max(edge, boxes[index].bottom);
        }}
        return atTop ? rect.top <= edge + 2 : rect.bottom >= edge - 2;
    }}

    function domPositionAtRowOffset(row, offset) {{
        const length = (row.textContent || "").length;
        const clamped = Math.max(0, Math.min(offset, length));
        return positionAtOffset(row, clamped);
    }}

    function extendSelectionToRowOffset(row, targetOffset) {{
        const selection = window.getSelection();
        if (!selection || typeof selection.setBaseAndExtent !== "function") {{
            return false;
        }}
        const position = domPositionAtRowOffset(row, targetOffset);
        try {{
            selection.setBaseAndExtent(
                selection.anchorNode,
                selection.anchorOffset,
                position.node,
                position.offset
            );
        }} catch (error) {{
            return false;
        }}
        const focusElement = position.node.nodeType === 1
            ? position.node
            : position.node.parentElement;
        if (focusElement) {{
            scrollRowIntoView(focusElement);
        }}
        return true;
    }}

    function selectionFocusInGap(selection) {{
        if (!selection || !selection.focusNode) {{
            return null;
        }}
        return closestWithin(selection.focusNode, "." + lineInputClass);
    }}

    function focusOffsetInsideGap(selection, gap) {{
        if (!gap || !selection.focusNode) {{
            return null;
        }}
        const range = document.createRange();
        try {{
            range.setStart(selection.focusNode, selection.focusOffset);
            range.collapse(true);
        }} catch (error) {{
            return null;
        }}
        return caretOffsetInInput(gap, range);
    }}

    function extendSelectionHorizontally(direction, byWord) {{
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || !selection.focusNode) {{
            return false;
        }}
        const row = closestWithin(selection.focusNode, "." + lineClass);
        if (!row) {{
            return false;
        }}
        const focusGap = selectionFocusInGap(selection);
        if (focusGap && !byWord) {{
            const inside = focusOffsetInsideGap(selection, focusGap);
            if (inside === null) {{
                return false;
            }}
            const gapLength = (focusGap.textContent || "").length;
            if (direction < 0 && inside > 0) {{
                return false;
            }}
            if (direction > 0 && inside < gapLength) {{
                return false;
            }}
        }}
        const position = rowOffsetForDomPosition(
            selection.focusNode, selection.focusOffset, row
        );
        if (!position) {{
            return false;
        }}
        const rowText = row.textContent || "";
        const target = byWord
            ? wordBoundaryOffset(rowText, position.offset, direction)
            : stepRowOffset(rowText, position.offset, direction);
        if (target < 0) {{
            const rows = lineRows();
            const current = rows.indexOf(row);
            const adjacent = rows[current + direction];
            if (!adjacent) {{
                return false;
            }}
            return extendSelectionToRowOffset(
                adjacent,
                direction < 0 ? (adjacent.textContent || "").length : 0
            );
        }}
        return extendSelectionToRowOffset(row, target);
    }}

    function rowOffsetForDomPosition(node, offset, knownRow) {{
        const row = knownRow || closestWithin(node, "." + lineClass);
        if (!row) {{
            return null;
        }}
        const value = offsetWithinRow(row, node, offset);
        if (value === null) {{
            return null;
        }}
        return {{ row: row, offset: value }};
    }}

    function extendSelectionVertically(direction) {{
        if (typeof document.caretRangeFromPoint !== "function") {{
            return false;
        }}
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0 || !selection.focusNode) {{
            return false;
        }}
        const focusRow = closestWithin(selection.focusNode, "." + lineClass);
        if (!focusRow) {{
            return false;
        }}
        const focusGap = selectionFocusInGap(selection);
        if (
            focusGap
            && !positionOnEdgeLine(
                focusGap,
                selection.focusNode,
                selection.focusOffset,
                direction < 0
            )
        ) {{
            return false;
        }}
        const caret = rectAtDomPosition(selection.focusNode, selection.focusOffset);
        if (!caret) {{
            return false;
        }}
        const height = Math.max(caret.bottom - caret.top, 8);
        const y = direction < 0
            ? caret.top - height / 2
            : caret.bottom + height / 2;
        const point = document.caretRangeFromPoint(caret.left, y);
        if (!point) {{
            return false;
        }}
        const targetRow = closestWithin(point.startContainer, "." + lineClass);
        if (!targetRow) {{
            return false;
        }}
        const host = point.startContainer.nodeType === 1
            ? point.startContainer
            : point.startContainer.parentElement;
        if (!host) {{
            return false;
        }}
        const boxRect = nearestLineBox(host, y);
        if (!boxRect) {{
            return false;
        }}
        if (direction < 0 && boxRect.bottom > caret.top + 2) {{
            return false;
        }}
        if (direction > 0 && boxRect.top < caret.bottom - 2) {{
            return false;
        }}
        try {{
            selection.setBaseAndExtent(
                selection.anchorNode,
                selection.anchorOffset,
                point.startContainer,
                point.startOffset
            );
        }} catch (error) {{
            return false;
        }}
        scrollRowIntoView(host);
        return true;
    }}

    function toggleSelectionStrike() {{
        const selection = window.getSelection();
        if (!selection || selection.isCollapsed || selection.rangeCount === 0) {{
            return false;
        }}
        const range = selection.getRangeAt(0);
        const texts = lineTexts();
        let firstLineInput = null;
        let changed = false;
        for (let index = 0; index < texts.length; index += 1) {{
            const textElement = texts[index];
            if (!range.intersectsNode(textElement)) {{
                continue;
            }}
            const content = collectTextCharacters(textElement);
            if (!content.chars.length) {{
                continue;
            }}
            const startOffset = offsetWithinElement(
                textElement, range.startContainer, range.startOffset
            );
            const endOffset = offsetWithinElement(
                textElement, range.endContainer, range.endOffset
            );
            if (startOffset === null || endOffset === null) {{
                continue;
            }}
            const from = Math.max(0, Math.min(startOffset, endOffset));
            const to = Math.min(
                content.chars.length, Math.max(startOffset, endOffset)
            );
            if (to <= from) {{
                continue;
            }}
            let allStruck = true;
            for (let position = from; position < to; position += 1) {{
                if (!content.struck[position]) {{
                    allStruck = false;
                    break;
                }}
            }}
            const nextStruck = !allStruck;
            for (let position = from; position < to; position += 1) {{
                content.struck[position] = nextStruck;
            }}
            rebuildTextElement(textElement, content.chars, content.struck);
            changed = true;
            if (!firstLineInput) {{
                const row = textElement.closest("." + lineClass);
                firstLineInput = trailingInput(row);
            }}
        }}
        if (!changed) {{
            return false;
        }}
        selection.removeAllRanges();
        reportStateSoon();
        focusInputAtEnd(firstLineInput);
        return true;
    }}

    function handleSelectionEnd() {{
        const selection = window.getSelection();
        if (!selection || selection.rangeCount === 0) {{
            return;
        }}
        if (!selection.isCollapsed) {{
            toggleSelectionStrike();
            return;
        }}
        const node = selection.getRangeAt(0).startContainer;
        if (closestWithin(node, "." + lineInputClass)) {{
            return;
        }}
        const textElement = closestWithin(node, "." + lineTextClass);
        if (!textElement) {{
            return;
        }}
        const point = window.typeBeforeAnswerV3LastPointer;
        window.typeBeforeAnswerV3LastPointer = null;
        if (point && point.allowGap) {{
            if (insertGapAtPoint(textElement, point.x, point.y)) {{
                reportStateSoon();
                return;
            }}
        }}
        const row = textElement.closest("." + lineClass);
        focusInputAtEnd(trailingInput(row));
    }}

    function removeInput() {{
        const container = document.getElementById(options.containerId);
        if (container) {{
            const root = linesRoot();
            const rootOptions = root && root.typeBeforeAnswerV3Options;
            if (rootOptions) {{
                if (rootOptions.answerSide) {{
                    pycmd(
                        rootOptions.htmlCommandPrefix
                            + encodeURIComponent(JSON.stringify(
                                collectLinesHtml(root)
                            ))
                    );
                    pycmd(
                        rootOptions.correctionsCommandPrefix
                            + encodeURIComponent(JSON.stringify(
                                collectCorrections(root)
                            ))
                    );
                }} else {{
                    pycmd(
                        rootOptions.textCommandPrefix
                            + encodeURIComponent((root && root.value) || "")
                    );
                }}
            }}
            container.remove();
        }}

        const wrapper = document.getElementById(options.wrapperId);
        if (wrapper && wrapper.parentElement) {{
            const parent = wrapper.parentElement;
            while (wrapper.firstChild) {{
                parent.insertBefore(wrapper.firstChild, wrapper);
            }}
            wrapper.remove();
        }}
    }}

    const middle = document.getElementById("middle");
    if (!middle) {{
        return;
    }}

    if (!options.showInput) {{
        stopAnswerObserver();
        removeInput();
        return;
    }}

    let wrapper = document.getElementById(options.wrapperId);
    if (!wrapper) {{
        wrapper = document.createElement("div");
        wrapper.id = options.wrapperId;
        wrapper.style.display = "flex";
        wrapper.style.flexDirection = "column";
        wrapper.style.alignItems = "center";
        wrapper.style.gap = "4px";
        wrapper.style.width = "100%";
        while (middle.firstChild) {{
            wrapper.appendChild(middle.firstChild);
        }}
        middle.appendChild(wrapper);
    }}

    let container = document.getElementById(options.containerId);
    if (!container) {{
        container = document.createElement("div");
        container.id = options.containerId;
        container.style.width = "100%";
        container.style.display = "flex";
        container.style.flexDirection = "column";
        container.style.alignItems = "center";
        container.style.margin = "0";
        wrapper.insertBefore(container, wrapper.firstChild);
    }} else if (wrapper.firstChild !== container) {{
        wrapper.insertBefore(container, wrapper.firstChild);
    }}

    const desiredKind = options.answerSide ? "rich" : "plain";
    let input = document.getElementById(options.inputId);
    let box = document.getElementById(options.boxId);
    let created = false;
    const currentKind = input ? input.dataset.typeBeforeAnswerV3Kind : null;
    if (currentKind !== desiredKind) {{
        const oldRoot = input;
        const oldOptions = oldRoot && oldRoot.typeBeforeAnswerV3Options;
        if (oldRoot && oldOptions) {{
            if (oldOptions.answerSide) {{
                pycmd(
                    oldOptions.htmlCommandPrefix
                        + encodeURIComponent(JSON.stringify(
                            collectLinesHtml(oldRoot)
                        ))
                );
                pycmd(
                    oldOptions.correctionsCommandPrefix
                        + encodeURIComponent(JSON.stringify(
                            collectCorrections(oldRoot)
                        ))
                );
            }} else {{
                pycmd(
                    oldOptions.textCommandPrefix
                        + encodeURIComponent(oldRoot.value || "")
                );
            }}
        }}
        if (box) {{
            box.remove();
            box = null;
        }}
        if (input) {{
            input.remove();
            input = null;
        }}
        created = true;
    }}

    if (options.answerSide) {{
        if (!box) {{
            box = document.createElement("div");
            box.id = options.boxId;
            box.dataset.typeBeforeAnswerV3Kind = desiredKind;
            container.appendChild(box);
            created = true;
        }}

        if (!input) {{
            input = document.createElement("div");
            input.id = options.inputId;
            input.dataset.typeBeforeAnswerV3Kind = desiredKind;
            input.setAttribute("role", "group");
            input.setAttribute("aria-label", "Type before answer");
            box.appendChild(input);
            created = true;
        }}
    }} else if (!input) {{
        created = true;
        input = document.createElement("textarea");
        input.id = options.inputId;
        input.dataset.typeBeforeAnswerV3 = "1";
        input.dataset.typeBeforeAnswerV3Kind = desiredKind;
        input.setAttribute("aria-label", "Type before answer");
        input.spellcheck = false;
        input.rows = 3;
        container.appendChild(input);
        box = input;
    }}

    box = options.answerSide ? document.getElementById(options.boxId) : input;

    box.style.boxShadow = "";
    box.style.boxSizing = "border-box";
    box.style.display = "block";
    box.style.width = options.widthPx ? options.widthPx + "px" : "min(100%, 720px)";
    box.style.maxWidth = "min(100%, " + options.maxWidthPx + "px)";
    box.style.maxHeight = options.maxHeightPx + "px";
    box.style.height = options.heightPx + "px";
    box.style.minWidth = options.minWidthPx + "px";
    box.style.minHeight = options.minHeightPx + "px";
    box.style.resize = "both";
    box.style.overflow = "auto";
    box.style.padding = "6px 8px";
    box.style.fontFamily = options.fontFamily;
    box.style.fontSize = options.fontSizePt + "pt";
    box.style.lineHeight = "1.35";
    box.style.border = "1px solid var(--border, #b8b8b8)";
    box.style.borderRadius = "4px";
    box.style.background = "var(--canvas, white)";
    box.style.color = "var(--fg, inherit)";
    box.style.whiteSpace = "pre-wrap";
    box.style.textAlign = "left";

    function styleLines(root) {{
        lineRows(root).forEach(function(row) {{
            row.style.display = "block";
            row.style.whiteSpace = "pre-wrap";
            row.style.textAlign = "left";
            row.style.minHeight = "1.35em";
            row.style.lineHeight = "1.35";
        }});
        lineTexts(root).forEach(function(textElement) {{
            applyTextSpanStyle(textElement);
        }});
        lineInputs(root).forEach(function(lineInput) {{
            applyLineInputStyle(lineInput);
        }});
    }}

    function applyTextSpanStyle(textElement) {{
        textElement.style.display = "inline";
        textElement.style.whiteSpace = "pre-wrap";
        textElement.style.textAlign = "left";
        textElement.style.outline = "none";
        textElement.style.userSelect = "text";
        textElement.style.setProperty("-webkit-user-select", "text");
        textElement.style.cursor = "text";
    }}

    function applyLineInputStyle(lineInput) {{
        lineInput.style.display = "inline";
        lineInput.style.whiteSpace = "pre-wrap";
        lineInput.style.textAlign = "left";
        lineInput.style.outline = "none";
        lineInput.style.border = "none";
        lineInput.style.background = "transparent";
        lineInput.style.padding = "0 1px";
        lineInput.style.margin = "0";
        lineInput.style.minWidth = "2px";
        lineInput.style.overflowWrap = "break-word";
        lineInput.style.setProperty("-webkit-user-select", "text");
        lineInput.style.setProperty("user-select", "text");
        lineInput.style.color = options.redColor;
        lineInput.style.caretColor = options.redColor;
        lineInput.style.fontFamily = options.fontFamily;
        lineInput.style.fontSize = options.fontSizePt + "pt";
        lineInput.style.lineHeight = "1.35";
    }}

    function createGapElement(lineIndex) {{
        const gap = document.createElement("span");
        gap.className = lineInputClass;
        gap.dataset.typeBeforeAnswerV3Line = String(lineIndex);
        gap.dataset.typeBeforeAnswerV3Gap = "1";
        gap.contentEditable = "true";
        gap.spellcheck = false;
        gap.setAttribute("role", "textbox");
        gap.setAttribute("aria-label", "Type before answer");
        applyLineInputStyle(gap);
        return gap;
    }}

    function makeTextSpan(lineIndex, fragment) {{
        const span = document.createElement("span");
        span.className = lineTextClass;
        span.dataset.typeBeforeAnswerV3Line = String(lineIndex);
        if (fragment) {{
            span.appendChild(fragment);
        }}
        applyTextSpanStyle(span);
        return span;
    }}

    function prepareRow(row, lineIndex) {{
        row.dataset.typeBeforeAnswerV3Line = String(lineIndex);
        Array.prototype.forEach.call(
            row.querySelectorAll("." + lineTextClass),
            function(element) {{
                element.dataset.typeBeforeAnswerV3Line = String(lineIndex);
                applyTextSpanStyle(element);
            }}
        );
        Array.prototype.forEach.call(
            row.querySelectorAll("." + lineInputClass),
            function(element) {{
                element.dataset.typeBeforeAnswerV3Line = String(lineIndex);
                element.contentEditable = "true";
                element.spellcheck = false;
                element.setAttribute("role", "textbox");
                element.setAttribute("aria-label", "Type before answer");
                applyLineInputStyle(element);
            }}
        );
    }}

    function storedRowHasGaps(stored) {{
        return (
            typeof stored === "string"
            && stored.indexOf(lineInputClass) >= 0
        );
    }}

    function serializeRow(row) {{
        const clone = row.cloneNode(true);
        Array.prototype.forEach.call(
            clone.querySelectorAll("." + lineTextClass),
            function(element) {{
                element.removeAttribute("style");
                element.removeAttribute("data-type-before-answer-v3-line");
            }}
        );
        Array.prototype.forEach.call(
            clone.querySelectorAll("." + lineInputClass),
            function(element) {{
                element.removeAttribute("style");
                element.removeAttribute("data-type-before-answer-v3-line");
                element.removeAttribute("data-type-before-answer-v3-gap");
                element.removeAttribute("contenteditable");
                element.removeAttribute("spellcheck");
                element.removeAttribute("role");
                element.removeAttribute("aria-label");
            }}
        );
        return clone.innerHTML;
    }}

    function collectLinesHtml(root) {{
        return lineRows(root).map(serializeRow);
    }}

    function collectCorrections(root) {{
        return lineRows(root).map(function(row) {{
            const gap = trailingInput(row);
            return gap ? (gap.innerHTML || "") : "";
        }});
    }}

    function insertGapAtPoint(textElement, x, y) {{
        if (typeof document.caretRangeFromPoint !== "function") {{
            return null;
        }}
        const row = closestWithin(textElement, "." + lineClass);
        if (!row) {{
            return null;
        }}
        const caret = document.caretRangeFromPoint(x, y);
        if (!caret || !textElement.contains(caret.startContainer)) {{
            return null;
        }}
        const lineIndex = Number(row.dataset.typeBeforeAnswerV3Line || 0);
        const beforeRange = document.createRange();
        beforeRange.selectNodeContents(textElement);
        try {{
            beforeRange.setEnd(caret.startContainer, caret.startOffset);
        }} catch (error) {{
            return null;
        }}
        const afterRange = document.createRange();
        afterRange.selectNodeContents(textElement);
        try {{
            afterRange.setStart(caret.startContainer, caret.startOffset);
        }} catch (error) {{
            return null;
        }}
        const beforeSpan = makeTextSpan(lineIndex, beforeRange.cloneContents());
        const afterSpan = makeTextSpan(lineIndex, afterRange.cloneContents());
        const gap = createGapElement(lineIndex);
        const parent = textElement.parentElement;
        if (!parent) {{
            return null;
        }}
        parent.insertBefore(beforeSpan, textElement);
        parent.insertBefore(gap, textElement);
        parent.insertBefore(afterSpan, textElement);
        parent.removeChild(textElement);
        if (!beforeSpan.textContent) {{
            beforeSpan.remove();
        }}
        if (!afterSpan.textContent) {{
            afterSpan.remove();
        }}
        gap.focus({{ preventScroll: true }});
        const selection = window.getSelection();
        if (selection) {{
            const range = document.createRange();
            range.selectNodeContents(gap);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
        }}
        scrollRowIntoView(gap);
        return gap;
    }}

    function removeGapIfEmpty(gap) {{
        if (!gap || !gap.isConnected) {{
            return false;
        }}
        const row = closestWithin(gap, "." + lineClass);
        if (!row || gap === row.lastElementChild) {{
            return false;
        }}
        if ((gap.textContent || "").length || gap.querySelector("br")) {{
            return false;
        }}
        const prev = gap.previousElementSibling;
        const next = gap.nextElementSibling;
        gap.remove();
        if (
            prev && next
            && prev.classList.contains(lineTextClass)
            && next.classList.contains(lineTextClass)
        ) {{
            while (next.firstChild) {{
                prev.appendChild(next.firstChild);
            }}
            next.remove();
        }}
        return true;
    }}

    function cleanupEmptyGaps() {{
        if (window.typeBeforeAnswerV3MouseDown) {{
            return false;
        }}
        const active = document.activeElement;
        let removed = false;
        lineRows().forEach(function(row) {{
            const gaps = Array.prototype.slice.call(
                row.querySelectorAll("." + lineInputClass)
            );
            gaps.forEach(function(gap) {{
                if (active && (gap === active || gap.contains(active))) {{
                    return;
                }}
                if (removeGapIfEmpty(gap)) {{
                    removed = true;
                }}
            }});
        }});
        if (removed) {{
            reportStateSoon();
        }}
        return removed;
    }}

    function rebuildLines(root) {{
        const textLines = splitLines(options.textValue || "");
        const storedLines = parseStoredArray(options.htmlValue);
        const storedCorrections = parseStoredArray(options.correctionsValue);
        root.innerHTML = "";
        for (let index = 0; index < textLines.length; index += 1) {{
            const row = document.createElement("div");
            row.className = lineClass;
            const storedLine = storedLines
                && typeof storedLines[index] === "string"
                ? storedLines[index]
                : null;
            if (storedRowHasGaps(storedLine)) {{
                row.innerHTML = storedLine;
                prepareRow(row, index);
            }} else {{
                const textElement = makeTextSpan(index, null);
                if (storedLine) {{
                    textElement.innerHTML = storedLine;
                }} else {{
                    textElement.textContent = textLines[index];
                }}
                const lineInput = createGapElement(index);
                if (
                    storedCorrections
                    && typeof storedCorrections[index] === "string"
                ) {{
                    lineInput.innerHTML = storedCorrections[index];
                }}
                row.appendChild(textElement);
                row.appendChild(lineInput);
            }}
            root.appendChild(row);
        }}
        root.dataset.typeBeforeAnswerV3Text = options.textValue || "";
        root.dataset.typeBeforeAnswerV3HtmlApplied = options.htmlValue || "";
        root.dataset.typeBeforeAnswerV3CorrectionsApplied =
            options.correctionsValue || "";
    }}

    ensureCorrectionsStyle();

    if (!options.answerSide) {{
        input.maxLength = options.maxLength;
        const previousSession = input.dataset.typeBeforeAnswerV3Session;
        const sessionChanged = previousSession !== String(options.session);
        if ((created || sessionChanged) && input.value !== (options.textValue || "")) {{
            input.value = options.textValue || "";
        }}
    }} else {{
        const previousSession = input.dataset.typeBeforeAnswerV3Session;
        const sessionChanged = previousSession !== String(options.session);
        const previousText = input.dataset.typeBeforeAnswerV3Text || "";
        const shouldRebuild =
            created
            || sessionChanged
            || lineRows(input).length === 0
            || previousText !== (options.textValue || "");
        if (shouldRebuild) {{
            rebuildLines(input);
        }} else if (
            options.htmlValue
            && options.htmlValue !== input.dataset.typeBeforeAnswerV3HtmlApplied
        ) {{
            const active = document.activeElement;
            if (!(active && closestWithin(active, "." + lineInputClass))) {{
                rebuildLines(input);
            }}
        }}
        styleLines(input);
    }}

    input.dataset.typeBeforeAnswerV3Session = String(options.session);
    input.typeBeforeAnswerV3Options = options;

    function currentOptions() {{
        return input.typeBeforeAnswerV3Options || options;
    }}

    function focusReviewer() {{
        reportStateNow();
        const active = document.activeElement;
        if (active && typeof active.blur === "function") {{
            active.blur();
        }}
        pycmd(currentOptions().focusCommand);
    }}

    function reportStateNow() {{
        if (input.typeBeforeAnswerV3TextTimer) {{
            clearTimeout(input.typeBeforeAnswerV3TextTimer);
            input.typeBeforeAnswerV3TextTimer = null;
        }}
        const activeOptions = currentOptions();
        if (activeOptions.answerSide) {{
            const root = linesRoot();
            if (!root) {{
                return;
            }}
            const strikes = JSON.stringify(collectLinesHtml(root));
            const corrections = JSON.stringify(collectCorrections(root));
            pycmd(
                activeOptions.htmlCommandPrefix
                    + encodeURIComponent(strikes)
            );
            pycmd(
                activeOptions.correctionsCommandPrefix
                    + encodeURIComponent(corrections)
            );
            root.dataset.typeBeforeAnswerV3HtmlApplied = strikes;
            root.dataset.typeBeforeAnswerV3CorrectionsApplied = corrections;
        }} else {{
            pycmd(
                activeOptions.textCommandPrefix
                    + encodeURIComponent(input.value || "")
            );
        }}
    }}

    function reportStateSoon() {{
        if (input.typeBeforeAnswerV3TextTimer) {{
            clearTimeout(input.typeBeforeAnswerV3TextTimer);
        }}
        input.typeBeforeAnswerV3TextTimer = setTimeout(function() {{
            input.typeBeforeAnswerV3TextTimer = null;
            reportStateNow();
        }}, currentOptions().textReportDelayMs);
    }}

    function reportSize() {{
        if (box.dataset.typeBeforeAnswerV3Reporting === "1") {{
            return;
        }}
        box.dataset.typeBeforeAnswerV3Reporting = "1";
        setTimeout(function() {{
            box.dataset.typeBeforeAnswerV3Reporting = "";
            const rect = box.getBoundingClientRect();
            const width = Math.round(rect.width);
            const height = Math.round(rect.height);
            if (width > 0 && height > 0) {{
                pycmd(currentOptions().sizeCommandPrefix + width + ":" + height);
            }}
        }}, 250);
    }}

    function beginSizeTrack() {{
        const rect = box.getBoundingClientRect();
        box.dataset.typeBeforeAnswerV3PointerWidth = String(Math.round(rect.width));
        box.dataset.typeBeforeAnswerV3PointerHeight = String(Math.round(rect.height));
        window.typeBeforeAnswerV3SizeDragging = true;
        if (window.typeBeforeAnswerV3SizeTimer) {{
            clearTimeout(window.typeBeforeAnswerV3SizeTimer);
        }}
        window.typeBeforeAnswerV3SizeTimer = setTimeout(function() {{
            window.typeBeforeAnswerV3SizeTimer = null;
            window.typeBeforeAnswerV3SizeDragging = false;
        }}, 20000);
    }}

    function endSizeTrack() {{
        if (!window.typeBeforeAnswerV3SizeDragging) {{
            return;
        }}
        window.typeBeforeAnswerV3SizeDragging = false;
        if (window.typeBeforeAnswerV3SizeTimer) {{
            clearTimeout(window.typeBeforeAnswerV3SizeTimer);
            window.typeBeforeAnswerV3SizeTimer = null;
        }}
        reportSizeIfChanged();
    }}

    function reportSizeIfChanged() {{
        const rect = box.getBoundingClientRect();
        const previousWidth = Number(box.dataset.typeBeforeAnswerV3PointerWidth || rect.width);
        const previousHeight = Number(box.dataset.typeBeforeAnswerV3PointerHeight || rect.height);
        box.dataset.typeBeforeAnswerV3PointerWidth = "";
        box.dataset.typeBeforeAnswerV3PointerHeight = "";
        if (Math.abs(rect.width - previousWidth) >= 1 || Math.abs(rect.height - previousHeight) >= 1) {{
            reportSize();
        }}
    }}

    if (!options.answerSide && !input.dataset.typeBeforeAnswerV3Handler) {{
        input.addEventListener("keydown", function(evt) {{
            if (evt.isComposing || evt.keyCode === 229) {{
                return;
            }}
            if (evt.key === "Enter" && (evt.ctrlKey || evt.metaKey) && !evt.shiftKey && !evt.altKey) {{
                evt.preventDefault();
                evt.stopPropagation();
                focusReviewer();
                return;
            }}
            if (evt.key === "Escape") {{
                evt.preventDefault();
                evt.stopPropagation();
                focusReviewer();
                return;
            }}
            if (evt.key === "Enter") {{
                evt.stopPropagation();
            }}
        }});

        input.addEventListener("input", reportStateSoon);
        input.addEventListener("change", reportStateNow);
        input.dataset.typeBeforeAnswerV3Handler = "1";
    }}

    if (options.answerSide && !box.dataset.typeBeforeAnswerV3Handler) {{
        box.addEventListener("keydown", function(evt) {{
            if (evt.isComposing || evt.keyCode === 229) {{
                return;
            }}
            if (
                evt.key === "Tab"
                && !evt.ctrlKey
                && !evt.metaKey
                && !evt.altKey
            ) {{
                evt.preventDefault();
                evt.stopPropagation();
                focusReviewer();
                return;
            }}
            if (evt.key === "Enter" && (evt.ctrlKey || evt.metaKey) && !evt.shiftKey && !evt.altKey) {{
                evt.preventDefault();
                evt.stopPropagation();
                focusReviewer();
                return;
            }}
            if (evt.key === "Escape") {{
                evt.preventDefault();
                evt.stopPropagation();
                focusReviewer();
                return;
            }}
            const lineInput = closestWithin(evt.target, "." + lineInputClass);
            if (!lineInput) {{
                return;
            }}
            const noShiftArrow = (
                !evt.shiftKey && !evt.metaKey && !evt.altKey
            );
            if (
                evt.shiftKey
                && !evt.metaKey
                && !evt.altKey
                && (
                    evt.key === "ArrowLeft"
                    || evt.key === "ArrowRight"
                    || evt.key === "ArrowUp"
                    || evt.key === "ArrowDown"
                )
            ) {{
                const selection = window.getSelection();
                if (selection && selection.rangeCount > 0) {{
                    let extended = false;
                    if (evt.key === "ArrowLeft" || evt.key === "ArrowRight") {{
                        extended = extendSelectionHorizontally(
                            evt.key === "ArrowLeft" ? -1 : 1,
                            evt.ctrlKey
                        );
                    }} else {{
                        extended = extendSelectionVertically(
                            evt.key === "ArrowUp" ? -1 : 1
                        );
                    }}
                    if (extended) {{
                        evt.preventDefault();
                        evt.stopPropagation();
                        return;
                    }}
                }}
            }}
            if (
                noShiftArrow
                && !evt.ctrlKey
                && (evt.key === "ArrowUp" || evt.key === "ArrowDown")
            ) {{
                const selection = window.getSelection();
                if (selection && selection.isCollapsed && selection.rangeCount > 0) {{
                    const upward = evt.key === "ArrowUp";
                    if (caretOnEdgeLine(lineInput, upward)) {{
                        if (
                            moveCaretVertically(lineInput, upward ? -1 : 1)
                            || moveToAdjacentLine(lineInput, upward ? -1 : 1)
                        ) {{
                            evt.preventDefault();
                            evt.stopPropagation();
                            return;
                        }}
                    }}
                }}
            }}
            if (
                noShiftArrow
                && (evt.key === "ArrowLeft" || evt.key === "ArrowRight")
            ) {{
                if (moveCaretHorizontally(
                    lineInput,
                    evt.key === "ArrowLeft" ? -1 : 1,
                    evt.ctrlKey
                )) {{
                    evt.preventDefault();
                    evt.stopPropagation();
                    return;
                }}
            }}
            if (evt.key === "Backspace" || evt.key === "Delete") {{
                const selection = window.getSelection();
                if (selection && !selection.isCollapsed && selection.rangeCount > 0) {{
                    const clamped = clampRangeToInput(
                        lineInput, selection.getRangeAt(0)
                    );
                    if (clamped && !clamped.collapsed) {{
                        selection.removeAllRanges();
                        selection.addRange(clamped);
                    }}
                }}
                if (selectionTouchesOriginal()) {{
                    evt.preventDefault();
                    return;
                }}
                const htmlBefore = lineInput.innerHTML;
                const forward = evt.key === "Delete";
                setTimeout(function() {{
                    if (!lineInput.isConnected || lineInput.innerHTML !== htmlBefore) {{
                        return;
                    }}
                    if (deleteInsideInput(lineInput, forward)) {{
                        reportStateSoon();
                    }}
                }}, 0);
            }}
            if (evt.key === "Enter") {{
                evt.stopPropagation();
            }}
        }});

        box.addEventListener("keyup", function(evt) {{
            if (evt.key !== "Shift" && evt.keyCode !== 16) {{
                return;
            }}
            const selection = window.getSelection();
            if (!selection || selection.isCollapsed || selection.rangeCount === 0) {{
                return;
            }}
            if (!selectionTouchesOriginal()) {{
                return;
            }}
            setTimeout(function() {{
                const current = window.getSelection();
                if (!current || current.isCollapsed || current.rangeCount === 0) {{
                    return;
                }}
                if (!selectionTouchesOriginal()) {{
                    return;
                }}
                toggleSelectionStrike();
            }}, 0);
        }});

        box.addEventListener("beforeinput", function(evt) {{
            const lineInput = closestWithin(evt.target, "." + lineInputClass);
            if (!lineInput) {{
                return;
            }}
            if (selectionTouchesOriginal()) {{
                evt.preventDefault();
                return;
            }}
        }});

        box.addEventListener("input", function(evt) {{
            if (closestWithin(evt.target, "." + lineInputClass)) {{
                reportStateSoon();
            }}
        }});

        box.addEventListener("cut", function(evt) {{
            if (selectionTouchesOriginal()) {{
                evt.preventDefault();
            }}
        }});

        box.addEventListener("paste", function(evt) {{
            if (!closestWithin(evt.target, "." + lineInputClass)) {{
                return;
            }}
            evt.preventDefault();
            if (selectionTouchesOriginal()) {{
                return;
            }}
            const clipboard = evt.clipboardData || window.clipboardData;
            const text = clipboard ? clipboard.getData("text/plain") : "";
            document.execCommand("insertText", false, text);
            reportStateSoon();
        }});

        box.addEventListener("drop", function(evt) {{
            if (!closestWithin(evt.target, "." + lineInputClass)) {{
                return;
            }}
            evt.preventDefault();
            if (selectionTouchesOriginal()) {{
                return;
            }}
            const data = evt.dataTransfer;
            const text = data ? data.getData("text/plain") : "";
            document.execCommand("insertText", false, text);
            reportStateSoon();
        }});

        box.addEventListener("mousedown", function(evt) {{
            window.typeBeforeAnswerV3MouseDown = true;
            if (evt.button !== 0) {{
                window.typeBeforeAnswerV3LastPointer = null;
            }} else if (evt.target === box || closestWithin(evt.target, "." + lineTextClass)) {{
                window.typeBeforeAnswerV3LastPointer = {{
                    x: evt.clientX,
                    y: evt.clientY,
                    allowGap: evt.detail <= 1
                }};
            }} else {{
                window.typeBeforeAnswerV3LastPointer = null;
            }}
            if (evt.target === box) {{
                const rect = box.getBoundingClientRect();
                const grip = 20;
                if (
                    evt.clientX >= rect.right - grip
                    && evt.clientY >= rect.bottom - grip
                ) {{
                    return;
                }}
                const fallback = lastTrailingInput();
                if (fallback) {{
                    evt.preventDefault();
                    focusInputAtEnd(fallback);
                }}
                return;
            }}
            if (closestWithin(evt.target, "." + lineInputClass)) {{
                return;
            }}
            if (closestWithin(evt.target, "." + lineTextClass)) {{
                return;
            }}
            const row = closestWithin(evt.target, "." + lineClass);
            if (!row) {{
                return;
            }}
            const lineInput = trailingInput(row);
            if (!lineInput) {{
                return;
            }}
            evt.preventDefault();
            focusInputAtEnd(lineInput);
        }});

        box.addEventListener("focusout", function(evt) {{
            if (!closestWithin(evt.target, "." + lineInputClass)) {{
                return;
            }}
            setTimeout(cleanupEmptyGaps, 0);
        }});

        box.addEventListener("click", function(evt) {{
            const lineInput = closestWithin(evt.target, "." + lineInputClass);
            if (!lineInput) {{
                return;
            }}
            if (document.activeElement !== lineInput) {{
                focusInputAtEnd(lineInput);
            }}
        }});

        box.dataset.typeBeforeAnswerV3Handler = "1";
    }}

    if (options.answerSide) {{
        window.typeBeforeAnswerV3SelectionEnd = handleSelectionEnd;
        window.typeBeforeAnswerV3CleanupGaps = cleanupEmptyGaps;
        function afterPointerEnd() {{
            window.typeBeforeAnswerV3MouseDown = false;
            const handler = window.typeBeforeAnswerV3SelectionEnd;
            setTimeout(function() {{
                if (typeof handler === "function") {{
                    handler();
                }}
                window.typeBeforeAnswerV3LastPointer = null;
                if (typeof window.typeBeforeAnswerV3CleanupGaps === "function") {{
                    window.typeBeforeAnswerV3CleanupGaps();
                }}
            }}, 0);
        }}
        if (!document.documentElement.dataset.typeBeforeAnswerV3StrikeHandler) {{
            document.addEventListener("mouseup", afterPointerEnd, true);
            document.addEventListener("touchend", afterPointerEnd, true);
            document.documentElement.dataset.typeBeforeAnswerV3StrikeHandler = "1";
        }}
    }} else {{
        window.typeBeforeAnswerV3SelectionEnd = null;
        window.typeBeforeAnswerV3CleanupGaps = null;
    }}

    if (!box.dataset.typeBeforeAnswerV3SizeHandler) {{
        box.addEventListener("pointerdown", beginSizeTrack);
        box.dataset.typeBeforeAnswerV3SizeHandler = "1";
    }}

    if (window.typeBeforeAnswerV3EndSizeTrack) {{
        document.removeEventListener(
            "pointerup", window.typeBeforeAnswerV3EndSizeTrack, true
        );
        document.removeEventListener(
            "pointercancel", window.typeBeforeAnswerV3EndSizeTrack, true
        );
    }}
    window.typeBeforeAnswerV3EndSizeTrack = endSizeTrack;
    document.addEventListener("pointerup", endSizeTrack, true);
    document.addEventListener("pointercancel", endSizeTrack, true);

    if (typeof ResizeObserver === "function") {{
        if (window.typeBeforeAnswerV3SizeObserver) {{
            window.typeBeforeAnswerV3SizeObserver.disconnect();
        }}
        const startRect = box.getBoundingClientRect();
        let observedWidth = Math.round(startRect.width);
        let observedHeight = Math.round(startRect.height);
        const sizeObserver = new ResizeObserver(function() {{
            if (!window.typeBeforeAnswerV3SizeDragging) {{
                return;
            }}
            const rect = box.getBoundingClientRect();
            const width = Math.round(rect.width);
            const height = Math.round(rect.height);
            if (width === observedWidth && height === observedHeight) {{
                return;
            }}
            observedWidth = width;
            observedHeight = height;
            reportSize();
        }});
        sizeObserver.observe(box);
        window.typeBeforeAnswerV3SizeObserver = sizeObserver;
    }}

    if (options.autoFocus) {{
        setTimeout(function() {{
            if (options.answerSide) {{
                const rows = lineRows();
                focusInputAtEnd(rows.length ? trailingInput(rows[0]) : null);
            }} else {{
                input.focus({{ preventScroll: true }});
            }}
        }}, options.focusDelayMs);
    }}

    stopAnswerObserver();
    if (options.answerSide && window.MutationObserver) {{
        const observer = new MutationObserver(function() {{
            if (!document.getElementById(options.inputId)) {{
                observer.disconnect();
                if (window[answerObserverKey] === observer) {{
                    window[answerObserverKey] = null;
                }}
                pycmd(options.restoreCommand);
            }}
        }});
        observer.observe(middle, {{ childList: true, subtree: true }});
        window[answerObserverKey] = observer;
    }}
}})();
"""

GET_VALUE_JS = f"""
(function() {{
    const el = document.getElementById({json.dumps(INPUT_ID)});
    if (!el) {{
        return {{ found: false, value: null }};
    }}
    const value = typeof el.value === "string" ? el.value : (el.innerText || "");
    return {{ found: true, value: value }};
}})();
"""

BOTTOM_HEIGHT_JS = """
(function() {
    const outer = document.getElementById("outer");
    if (!outer) {
        return null;
    }
    const bodyStyle = window.getComputedStyle(document.body);
    const marginBottom = Number.parseFloat(bodyStyle.marginBottom) || 0;
    return Math.ceil(outer.getBoundingClientRect().bottom + marginBottom);
})();
"""


def _focus_reviewer(reviewer: Optional[Reviewer]) -> None:
    if not reviewer or reviewer is not _current_reviewer():
        return

    def _focus() -> None:
        if reviewer is not _current_reviewer():
            return
        try:
            web = getattr(reviewer, "web", None) or reviewer.mw.web
            web.setFocus()
            reviewer.mw.activateWindow()
        except Exception:
            logger.exception("Could not move focus back to the reviewer")

    reviewer.mw.progress.single_shot(0, _focus)


def _adjust_bottom_height(reviewer: Optional[Reviewer]) -> None:
    if not reviewer or reviewer is not _current_reviewer() or not _bottom_web_available(reviewer):
        return

    web = reviewer.bottom.web
    session = _reviewer_session(reviewer)
    card = getattr(reviewer, "card", None)

    def _apply_height(value: Any) -> None:
        if (
            reviewer is not _current_reviewer()
            or getattr(reviewer, "card", None) is not card
            or _reviewer_session(reviewer) != session
            or not _bottom_web_available(reviewer)
        ):
            return
        height = _optional_clamped_int(value, 1, MAX_BOX_HEIGHT_PX + 600)
        if height is not None:
            try:
                reviewer.bottom.web.setFixedHeight(height)
            except Exception:
                logger.debug("Reviewer bottom bar was deleted before resizing", exc_info=True)

    try:
        web.evalWithCallback(BOTTOM_HEIGHT_JS, _apply_height)
    except Exception:
        logger.exception("Could not adjust reviewer bottom bar height")


def _save_box_size(raw_size: str, reviewer: Optional[Reviewer]) -> None:
    if len(raw_size) > 32:
        return
    try:
        raw_width, raw_height = raw_size.split(":", 1)
    except ValueError:
        return

    width = _optional_clamped_int(raw_width, MIN_BOX_WIDTH_PX, MAX_BOX_WIDTH_PX)
    height = _optional_clamped_int(raw_height, MIN_BOX_HEIGHT_PX, MAX_BOX_HEIGHT_PX)
    if width is None or height is None:
        return
    state = _get_state()
    if state.get("box_width_px") == width and state.get("box_height_px") == height:
        return
    state["box_width_px"] = width
    state["box_height_px"] = height
    _save_state(state)
    _adjust_bottom_height(reviewer)


def _save_text(encoded_text: str, reviewer: Optional[Reviewer]) -> None:
    if len(encoded_text) > MAX_TEXT_LENGTH * 12:
        return
    _set_reviewer_text(reviewer, unquote(encoded_text))


def _save_html(encoded_html: str, reviewer: Optional[Reviewer]) -> None:
    if len(encoded_html) > MAX_HTML_LENGTH * 12:
        return
    _set_reviewer_html(reviewer, unquote(encoded_html))


def _save_corrections(encoded_html: str, reviewer: Optional[Reviewer]) -> None:
    if len(encoded_html) > MAX_HTML_LENGTH * 12:
        return
    _set_reviewer_corrections(reviewer, unquote(encoded_html))


def _on_js_message(handled: tuple[bool, Any], message: str, context: Any) -> tuple[bool, Any]:
    if handled[0] or not message.startswith(COMMAND_PREFIX):
        return handled

    reviewer = _reviewer_from_context(context)
    focus_payload = _message_for_current_session(message, f"{FOCUS_COMMAND}:", reviewer)
    if focus_payload is not None:
        if focus_payload == "":
            _focus_reviewer(reviewer)
        return (True, None)

    restore_payload = _message_for_current_session(message, f"{RESTORE_COMMAND}:", reviewer)
    if restore_payload is not None:
        if (
            restore_payload == ""
            and reviewer is not None
            and getattr(reviewer, "state", None) == "answer"
            and _is_enabled()
        ):
            _insert_or_refresh_ui(reviewer, show_input=True, auto_focus=False)
        return (True, None)

    text_payload = _message_for_current_session(message, TEXT_COMMAND_PREFIX, reviewer)
    if text_payload is not None:
        _save_text(text_payload, reviewer)
        return (True, None)

    html_payload = _message_for_current_session(message, HTML_COMMAND_PREFIX, reviewer)
    if html_payload is not None:
        _save_html(html_payload, reviewer)
        return (True, None)

    corrections_payload = _message_for_current_session(
        message, CORRECTIONS_COMMAND_PREFIX, reviewer
    )
    if corrections_payload is not None:
        _save_corrections(corrections_payload, reviewer)
        return (True, None)

    size_payload = _message_for_current_session(message, SIZE_COMMAND_PREFIX, reviewer)
    if size_payload is not None:
        _save_box_size(size_payload, reviewer)
        return (True, None)

    # The prefix belongs to this add-on. Consume malformed, stale, or messages
    # originating outside the reviewer bottom bar without acting on them.
    return (True, None)


def _insert_or_refresh_ui(reviewer: Reviewer, show_input: bool, auto_focus: bool) -> None:
    if not _bottom_web_available(reviewer):
        return
    session = _reviewer_session(reviewer)
    card = getattr(reviewer, "card", None)
    try:
        reviewer.bottom.web.eval(
            _build_review_ui_js(reviewer, show_input=show_input, auto_focus=auto_focus)
        )
    except Exception:
        logger.exception("Could not update the Type Before Answer review UI")
        return

    def _still_current() -> bool:
        return (
            reviewer is _current_reviewer()
            and getattr(reviewer, "card", None) is card
            and _reviewer_session(reviewer) == session
        )

    def _adjust_height() -> None:
        if _still_current():
            _adjust_bottom_height(reviewer)

    reviewer.mw.progress.single_shot(FOCUS_DELAY_MS, _adjust_height)

    def _focus_bottom() -> None:
        if (
            _still_current()
            and show_input
            and auto_focus
            and getattr(reviewer, "state", None) == "question"
            and _is_enabled()
            and _bottom_web_available(reviewer)
        ):
            try:
                reviewer.bottom.web.setFocus()
            except Exception:
                logger.debug("Reviewer bottom bar was deleted before focusing", exc_info=True)

    reviewer.mw.progress.single_shot(FOCUS_DELAY_MS, _focus_bottom)


def _refresh_current_review_ui() -> None:
    reviewer = _current_reviewer()
    if not reviewer:
        return
    _insert_or_refresh_ui(reviewer, show_input=True, auto_focus=False)


def _card_key(card: Any) -> Any:
    card_id = getattr(card, "id", None)
    return card_id if card_id is not None else id(card)


def _on_show_question(card: Any) -> None:  # type: ignore[override]
    reviewer = _current_reviewer()
    if reviewer:
        card_key = _card_key(card)
        is_new_question = (
            getattr(reviewer, _CARD_ATTRIBUTE, None) != card_key
            or getattr(reviewer, _SIDE_ATTRIBUTE, None) != "question"
        )
        if is_new_question:
            _advance_reviewer_session(reviewer)
            _set_reviewer_text(reviewer, "")
            _set_reviewer_html(reviewer, "")
            _set_reviewer_corrections(reviewer, "")
        setattr(reviewer, _CARD_ATTRIBUTE, card_key)
        setattr(reviewer, _SIDE_ATTRIBUTE, "question")
        _insert_or_refresh_ui(
            reviewer,
            show_input=True,
            auto_focus=_focus_text_box_on_show_question(),
        )


def _on_show_answer(card: Any) -> None:  # type: ignore[override]
    reviewer = _current_reviewer()
    if reviewer:
        was_on_answer = getattr(reviewer, _SIDE_ATTRIBUTE, None) == "answer"
        setattr(reviewer, _CARD_ATTRIBUTE, _card_key(card))
        setattr(reviewer, _SIDE_ATTRIBUTE, "answer")
        if not was_on_answer:
            _set_reviewer_html(reviewer, "")
            _set_reviewer_corrections(reviewer, "")
        _insert_or_refresh_ui(reviewer, show_input=True, auto_focus=False)


def _on_reviewer_will_end() -> None:
    reviewer = getattr(aqt.mw, "reviewer", None)
    if not isinstance(reviewer, Reviewer):
        return
    _advance_reviewer_session(reviewer)
    _set_reviewer_text(reviewer, "")
    _set_reviewer_html(reviewer, "")
    _set_reviewer_corrections(reviewer, "")
    setattr(reviewer, _CARD_ATTRIBUTE, None)
    setattr(reviewer, _SIDE_ATTRIBUTE, None)


def _patch_reviewer_methods() -> None:
    original_show_answer = getattr(Reviewer, _ORIGINAL_SHOW_ANSWER_ATTRIBUTE, None)
    if original_show_answer is None:
        original_show_answer = getattr(Reviewer, "_showAnswer", None)
    if not callable(original_show_answer):
        logger.error("Type Before Answer could not find Reviewer._showAnswer")
        return
    if not hasattr(Reviewer, _ORIGINAL_SHOW_ANSWER_ATTRIBUTE):
        setattr(Reviewer, _ORIGINAL_SHOW_ANSWER_ATTRIBUTE, original_show_answer)

    def _show_answer_with_text_capture(self: Reviewer) -> None:
        original = getattr(Reviewer, _ORIGINAL_SHOW_ANSWER_ATTRIBUTE)
        if (
            not _is_enabled()
            or getattr(self, "state", None) != "question"
            or not _bottom_web_available(self)
        ):
            original(self)
            return

        session = _reviewer_session(self)
        card = getattr(self, "card", None)

        def _after(result: Any) -> None:
            if (
                self is not _current_reviewer()
                or getattr(self, "card", None) is not card
                or _reviewer_session(self) != session
            ):
                return
            if isinstance(result, dict) and result.get("found") is True:
                value = result.get("value")
                if isinstance(value, str):
                    _set_reviewer_text(self, value)
            if getattr(self, "state", None) == "answer":
                _insert_or_refresh_ui(self, show_input=True, auto_focus=False)

        try:
            self.bottom.web.evalWithCallback(GET_VALUE_JS, _after)
        except Exception:
            logger.exception("Could not capture Type Before Answer text before showing answer")
        # Never block Anki's answer transition on a webview callback. JavaScript
        # requests are queued in order, and the callback only refreshes the
        # preserved text if it arrives for the same card session.
        original(self)

    Reviewer._showAnswer = _show_answer_with_text_capture  # type: ignore[assignment]


def _sync_type_menu() -> None:
    if not _toggle_action:
        return
    enabled = _is_enabled()
    _toggle_action.blockSignals(True)
    _toggle_action.setChecked(enabled)
    _toggle_action.setText("Type box: On" if enabled else "Type box: Off")
    _toggle_action.blockSignals(False)


def _on_toggle_action_triggered(*_args: Any) -> None:
    if _toggle_action:
        _set_enabled(_toggle_action.isChecked())


def _sync_focus_action() -> None:
    if not _focus_action:
        return
    enabled = _focus_text_box_on_show_question()
    _focus_action.blockSignals(True)
    _focus_action.setChecked(enabled)
    _focus_action.setText(
        "Auto-focus box: On" if enabled else "Auto-focus box: Off"
    )
    _focus_action.blockSignals(False)


def _set_focus_text_box(enabled: bool) -> None:
    config = _get_config()
    config["focus_text_box"] = enabled
    try:
        mw.addonManager.writeConfig(__name__, config)
    except Exception:
        logger.exception("Could not save the Type Before Answer focus setting")
        _sync_focus_action()
        tooltip(
            f"{ADDON_LABEL}: could not save the focus setting",
            parent=mw,
            period=2000,
        )
        return
    _sync_focus_action()
    tooltip(
        f"{ADDON_LABEL}: auto-focus {'On' if enabled else 'Off'}",
        parent=mw,
        period=1200,
    )
    if enabled:
        reviewer = _current_reviewer()
        if reviewer and getattr(reviewer, "state", None) == "question":
            _insert_or_refresh_ui(reviewer, show_input=True, auto_focus=True)


def _on_focus_action_triggered(*_args: Any) -> None:
    if _focus_action:
        _set_focus_text_box(_focus_action.isChecked())


def _setup_type_menu() -> None:
    global _type_menu, _toggle_action, _focus_action
    if _type_menu or not mw:
        return

    try:
        menu_bar = getattr(mw.form, "menubar", None) or mw.menuBar()
        for action in list(menu_bar.actions()):
            menu = action.menu()
            if menu and menu.objectName() == "typeBeforeAnswerV3Menu":
                menu_bar.removeAction(action)

        _type_menu = QMenu("Type", mw)
        _type_menu.setObjectName("typeBeforeAnswerV3Menu")
        help_menu = getattr(mw.form, "menuHelp", None)
        if help_menu:
            menu_bar.insertMenu(help_menu.menuAction(), _type_menu)
        else:
            menu_bar.addMenu(_type_menu)

        _toggle_action = QAction("Type box: On", mw)
        _toggle_action.setObjectName("typeBeforeAnswerV3ToggleAction")
        _toggle_action.setCheckable(True)
        _toggle_action.setToolTip("Show or hide the Type Before Answer box during review.")
        _toggle_action.triggered.connect(_on_toggle_action_triggered)
        _type_menu.addAction(_toggle_action)

        _type_menu.addSeparator()

        _focus_action = QAction("Auto-focus box: Off", mw)
        _focus_action.setObjectName("typeBeforeAnswerV3FocusAction")
        _focus_action.setCheckable(True)
        _focus_action.setToolTip(
            "Focus the typing box automatically when a new card is shown."
        )
        _focus_action.triggered.connect(_on_focus_action_triggered)
        _type_menu.addAction(_focus_action)
        _sync_type_menu()
        _sync_focus_action()
    except Exception:
        logger.exception("Could not add Type Before Answer menu")


def _on_config_updated(*_args: Any) -> None:
    _sync_focus_action()
    _refresh_current_review_ui()


def _register_hooks() -> None:
    previous_hooks = getattr(gui_hooks, _HOOK_REGISTRY_ATTRIBUTE, ())
    for hook_name, callback in previous_hooks:
        hook = getattr(gui_hooks, hook_name, None)
        if hook is None:
            continue
        try:
            hook.remove(callback)
        except (ValueError, AttributeError):
            pass

    hooks = [
        ("reviewer_did_show_question", _on_show_question),
        ("webview_did_receive_js_message", _on_js_message),
    ]
    if hasattr(gui_hooks, "reviewer_did_show_answer"):
        hooks.append(("reviewer_did_show_answer", _on_show_answer))
    if hasattr(gui_hooks, "reviewer_will_end"):
        hooks.append(("reviewer_will_end", _on_reviewer_will_end))

    for hook_name, callback in hooks:
        getattr(gui_hooks, hook_name).append(callback)
    setattr(gui_hooks, _HOOK_REGISTRY_ATTRIBUTE, tuple(hooks))


def _setup() -> None:
    _patch_reviewer_methods()
    _setup_type_menu()
    _register_hooks()
    if mw and hasattr(mw.addonManager, "setConfigUpdatedAction"):
        mw.addonManager.setConfigUpdatedAction(__name__, _on_config_updated)


_setup()
