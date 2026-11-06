from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

from lxml import etree
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, Image

from ecuador_einvoicing.protocol import PARSER

NAME_SIZE = 0.7
NAME_GAP = 0.27
EM = 1000
SVG = "{http://www.w3.org/2000/svg}"
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
HEX = re.compile(r"#[0-9A-Fa-f]{6}")
STRAIGHT = re.compile(r"[MLZ\d\s.,-]+")
TRANSLATE = "translate"
NONE = "none"
CURRENT_INK = "currentColor"


class Logo(Protocol):
    def flowable(self, height: float, one_ink: bool) -> Flowable: ...


@dataclass(frozen=True)
class ImageLogo:
    path: Path
    one_ink_path: Path | None = None

    def flowable(self, height: float, one_ink: bool) -> Flowable:
        source = str(self.one_ink_path if one_ink and self.one_ink_path else self.path)
        wide, tall = ImageReader(source).getSize()
        return Image(source, width=height * wide / tall, height=height)


@dataclass(frozen=True)
class MarkLogo:
    """A mark of straight shapes and a name in its own typeface, both read from SVG files."""

    mark: Path
    name: Path
    font: Path
    name_size: float = NAME_SIZE
    gap: float = NAME_GAP

    def flowable(self, height: float, one_ink: bool) -> Flowable:
        return Lockup(self, height, colors.black if one_ink else None)


@dataclass(frozen=True)
class Paint:
    fill: str | None
    stroke: str | None
    width: float


@dataclass(frozen=True)
class Name:
    runs: tuple[tuple[str, str | None], ...]
    tracking: float


UNPAINTED = Paint(fill=CURRENT_INK, stroke=None, width=1.0)


@cache
def _svg(path: Path) -> etree._Element:
    return etree.fromstring(path.read_bytes(), PARSER)


@cache
def _font(path: Path) -> str:
    pdfmetrics.registerFont(TTFont(path.stem, str(path)))
    return path.stem


@cache
def _name(path: Path) -> Name:
    text = next(_svg(path).iter(f"{SVG}text"))
    fill = text.get("fill")
    runs: list[tuple[str | None, str | None]] = [(text.text, fill)]
    for span in text:
        runs += [(span.text, span.get("fill", fill)), (span.tail, fill)]
    tracking = float(text.get("letter-spacing", "0")) / float(text.get("font-size", "1"))
    return Name(tuple((words, paint) for words, paint in runs if words), tracking)


def _baseline(font: str, box: float, size: float) -> float:
    face = pdfmetrics.getFont(font).face
    ascent, descent = face.ascent / EM, -face.descent / EM
    return (box - size) / 2 + (size - (ascent + descent) * size) / 2 + ascent * size


def _name_width(name: Name, font: str, size: float) -> float:
    letters = sum(len(words) for words, _ in name.runs)
    drawn = sum(pdfmetrics.stringWidth(words, font, size) for words, _ in name.runs)
    return drawn + letters * name.tracking * size


def _inherited(node: etree._Element, paint: Paint) -> Paint:
    return Paint(
        fill=node.get("fill", paint.fill),
        stroke=node.get("stroke", paint.stroke),
        width=float(node.get("stroke-width", paint.width)),
    )


def _colour(value: str | None, ink: colors.Color | None) -> colors.Color | None:
    """A paint as the SVG writes it, a CSS variable read by its fallback; one ink overrides all."""
    if value is None or value == NONE:
        return None
    if ink is not None:
        return ink
    found = HEX.search(value)
    return colors.HexColor(found.group()) if found else colors.black


def _numbers(node: etree._Element, *names: str) -> list[float]:
    return [float(node.get(name, "0")) for name in names]


class Lockup(Flowable):
    def __init__(self, logo: MarkLogo, height: float, ink: colors.Color | None) -> None:
        super().__init__()
        self.logo = logo
        self.font = _font(logo.font)
        self.size = height * logo.name_size
        self.offset = height * (1 + logo.gap)
        self.ink = ink
        self.width = self.offset + _name_width(_name(logo.name), self.font, self.size)
        self.height = height

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return self.width, self.height

    def draw(self) -> None:
        drawing = _svg(self.logo.mark)
        left, top, wide, _ = (float(value) for value in NUMBER.findall(drawing.get("viewBox", "")))
        scale = self.height / wide
        canvas = self.canv
        canvas.saveState()
        canvas.translate(0, self.height)
        canvas.scale(scale, -scale)
        canvas.translate(-left, -top)
        self._group(drawing, UNPAINTED)
        canvas.restoreState()
        self._write()

    def _write(self) -> None:
        name = _name(self.logo.name)
        baseline = self.height - _baseline(self.font, self.height, self.size)
        written = self.canv.beginText(self.offset, baseline)
        written.setFont(self.font, self.size)
        written.setCharSpace(name.tracking * self.size)
        for words, paint in name.runs:
            written.setFillColor(_colour(paint, self.ink) or colors.black)
            written.textOut(words)
        self.canv.drawText(written)

    def _brush(self, paint: Paint) -> tuple[int, int]:
        fill = _colour(paint.fill, self.ink)
        stroke = _colour(paint.stroke, self.ink)
        if fill is not None:
            self.canv.setFillColor(fill)
        if stroke is not None:
            self.canv.setStrokeColor(stroke)
            self.canv.setLineWidth(paint.width)
        return int(stroke is not None), int(fill is not None)

    def _group(self, node: etree._Element, paint: Paint) -> None:
        paint = _inherited(node, paint)
        moved = node.get("transform", "")
        if moved and not moved.startswith(TRANSLATE):
            raise ValueError(f"The mark moves a group in a way it cannot draw: {moved}")
        shift = [float(value) for value in NUMBER.findall(moved)] + [0.0, 0.0]
        self.canv.saveState()
        self.canv.translate(shift[0], shift[1])
        for child in node:
            tag = str(child.tag).removeprefix(SVG)
            if tag == "g":
                self._group(child, paint)
            elif tag == "rect":
                self._rect(child, _inherited(child, paint))
            elif tag == "path":
                self._path(child, _inherited(child, paint))
            else:
                raise ValueError(f"The mark holds a shape it cannot draw: {tag}")
        self.canv.restoreState()

    def _rect(self, node: etree._Element, paint: Paint) -> None:
        stroke, fill = self._brush(paint)
        x, y, wide, tall, corner = _numbers(node, "x", "y", "width", "height", "rx")
        self.canv.roundRect(x, y, wide, tall, corner, stroke=stroke, fill=fill)

    def _path(self, node: etree._Element, paint: Paint) -> None:
        outline = node.get("d", "")
        if not STRAIGHT.fullmatch(outline):
            raise ValueError("The mark draws a curve it cannot follow")
        stroke, fill = self._brush(paint)
        values = [float(value) for value in NUMBER.findall(outline)]
        points = list(zip(values[::2], values[1::2], strict=True))
        drawn = self.canv.beginPath()
        drawn.moveTo(*points[0])
        for point in points[1:]:
            drawn.lineTo(*point)
        drawn.close()
        self.canv.drawPath(drawn, stroke=stroke, fill=fill)
