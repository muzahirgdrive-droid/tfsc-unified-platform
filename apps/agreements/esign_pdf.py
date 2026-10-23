"""PDF work for e-signature: inspection, page images, field auto-placement, final stamping and the certificate.

Coordinates of fields are PDF points in the page *as displayed* (rotation applied), measured from the top-left corner.
Stamping converts them back into each page's own coordinate space, so rotated and offset pages are handled.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import subprocess
import tempfile
import zoneinfo
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PdfReadError
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MAX_PAGES = 200
INK = colors.HexColor('#1D2B64')
MUTED = colors.HexColor('#5B6475')
ACCENT = colors.HexColor('#1C7CD5')

_FONT_DIRS = ('/usr/share/fonts/truetype/liberation2', '/usr/share/fonts/truetype/liberation')
_fonts = None


def fonts():
    """Regular/bold TrueType fonts with wide Latin coverage; Helvetica only if none is installed."""
    global _fonts
    if _fonts is None:
        _fonts = ('Helvetica', 'Helvetica-Bold')
        for folder in _FONT_DIRS:
            regular, bold = Path(folder) / 'LiberationSans-Regular.ttf', Path(folder) / 'LiberationSans-Bold.ttf'
            if regular.is_file() and bold.is_file():
                pdfmetrics.registerFont(TTFont('TFSCSans', str(regular)))
                pdfmetrics.registerFont(TTFont('TFSCSans-Bold', str(bold)))
                _fonts = ('TFSCSans', 'TFSCSans-Bold')
                break
    return _fonts


def _safe_text(value: str, font: str) -> str:
    text = ''.join(ch for ch in str(value) if ch in ' \n' or ch.isprintable())
    if font.startswith('Helvetica'):
        text = text.encode('latin-1', 'replace').decode('latin-1')
    return text


# ------------------------------------------------------------------ inspection and page images
def inspect(raw: bytes) -> list[list[float]]:
    """Validate a PDF and return [[width, height, rotation], ...] of each page as displayed."""
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted:
            raise ValueError('This PDF is password-protected. Remove the password and upload it again.')
        count = len(reader.pages)
        if not 1 <= count <= MAX_PAGES:
            raise ValueError(f'A document must have between 1 and {MAX_PAGES} pages.')
        sizes = []
        for page in reader.pages:
            box = page.mediabox
            width, height = float(box.width), float(box.height)
            rotation = int(page.rotation or 0) % 360
            if rotation not in (0, 90, 180, 270):
                raise ValueError('A page has an unsupported rotation.')
            if not (36 <= width <= 5000 and 36 <= height <= 5000):
                raise ValueError('A page has an unsupported size.')
            sizes.append([round(height, 2), round(width, 2), rotation] if rotation in (90, 270) else [round(width, 2), round(height, 2), rotation])
        return sizes
    except ValueError:
        raise
    except (PdfReadError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise ValueError('This file is not a readable PDF.') from exc


def render_page(raw: bytes, page: int, dpi: int, cache: Path | None = None) -> bytes:
    """PNG of one page (poppler's pdftoppm), cached on disk when a cache path is given."""
    if cache and cache.is_file():
        return cache.read_bytes()
    tool = os.getenv('PDFTOPPM_BIN', 'pdftoppm')
    with tempfile.TemporaryDirectory(prefix='tfsc-page-') as folder:
        source = Path(folder) / 'in.pdf'
        source.write_bytes(raw)
        try:
            result = subprocess.run([tool, '-f', str(page), '-l', str(page), '-r', str(dpi), '-png', '-singlefile', str(source), str(Path(folder) / 'out')],
                                    capture_output=True, timeout=60)
        except FileNotFoundError:
            raise RuntimeError('The page renderer (poppler-utils) is not installed on this server.') from None
        except subprocess.TimeoutExpired:
            raise RuntimeError('This page took too long to display.') from None
        out = Path(folder) / 'out.png'
        if result.returncode or not out.is_file():
            raise RuntimeError('This page could not be displayed.')
        png = out.read_bytes()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temp = cache.with_suffix('.tmp')
        temp.write_bytes(png)
        temp.replace(cache)
    return png


# ------------------------------------------------------------------ automatic field placement for generated agreements
def _spans(page) -> list[tuple[float, float, str]]:
    spans = []

    def visit(text, cm, tm, font, size):
        if text.strip():
            a, b, c, d, e, f = tm
            A, B, C, D, E, F = cm
            spans.append((e * A + f * C + E, e * B + f * D + F, text.strip()))
    page.extract_text(visitor_text=visit)
    return spans


def execution_anchors(raw: bytes) -> dict | None:
    """Find the signature table of a generated agreement: the last page with two "Signature:" labels.

    Returns {'page': n, 'company': {...}, 'client': {...}} with each label's left x, baseline (display space, from the
    top) and the text after the label, or None when the layout is not one this application generated.
    """
    reader = PdfReader(io.BytesIO(raw), strict=False)
    for index in range(len(reader.pages) - 1, max(-1, len(reader.pages) - 4), -1):
        page = reader.pages[index]
        if int(page.rotation or 0) % 360:
            continue
        height = float(page.mediabox.height)
        bottom = float(page.mediabox.bottom)
        spans = _spans(page)
        labels = [s for s in spans if s[2].startswith('Signature:')]
        pairs = [(a, b) for a in labels for b in labels if a[0] < b[0] - 50 and abs(a[1] - b[1]) < 1]
        if not pairs:
            continue
        left, right = min(pairs, key=lambda pair: pair[0][1])  # the lowest pair on the page is the signature table
        company_x, client_x, baseline = left[0], right[0], left[1]
        sides = {}
        for side, x in (('company', company_x), ('client', client_x)):
            found = {}
            for sx, sy, text in spans:
                if abs(sx - x) > 2 or sy > baseline + 1 or sy < baseline - 160:
                    continue
                for key, label in (('signature', 'Signature:'), ('name', 'Name:'), ('title', 'Title:'), ('date', 'Date:')):
                    if text.startswith(label) and key not in found:
                        rest = text[len(label):].strip()
                        found[key] = {'x': sx, 'baseline': height - (sy - bottom), 'label': label, 'blank': not rest.strip('_ ')}
            if {'signature', 'date'} <= set(found):
                sides[side] = found
        if {'company', 'client'} <= set(sides):
            return {'page': index + 1, **sides}
    return None


def label_width(label: str, size: float = 11) -> float:
    return pdfmetrics.stringWidth(label + ' ', 'Times-Roman', size)


def placement(anchor: dict, kind: str) -> dict:
    """Field box beside a signature-table label (display space)."""
    x = anchor['x'] + label_width(anchor['label']) + 2
    base = anchor['baseline']
    if kind == 'signature':
        return {'x': round(x, 1), 'y': round(base - 46, 1), 'w': 170.0, 'h': 42.0}
    return {'x': round(x, 1), 'y': round(base - 13, 1), 'w': 150.0, 'h': 16.0}


def initials_box(size: list[float], bottom_margin: float | None) -> dict:
    """Default spot for per-page initials: left of the footer page counter, just below the body text."""
    width, height = size[0], size[1]
    if bottom_margin:
        return {'x': 54.0, 'y': round(height - bottom_margin + 10, 1), 'w': 64.0, 'h': 24.0}
    return {'x': round(width - 100, 1), 'y': round(height - 50, 1), 'w': 64.0, 'h': 24.0}


# ------------------------------------------------------------------ stamping the completed documents
def _display_to_user(rotation: int, width: float, height: float, llx: float, lly: float) -> Transformation:
    """Map the overlay drawn in display space (origin bottom-left) onto the page's user space.

    width/height are the page's media box size (unrotated). /Rotate turns the page clockwise when displayed.
    """
    if rotation == 90:
        return Transformation((0, 1, -1, 0, width + llx, lly))
    if rotation == 180:
        return Transformation((-1, 0, 0, -1, width + llx, height + lly))
    if rotation == 270:
        return Transformation((0, -1, 1, 0, llx, height + lly))
    return Transformation((1, 0, 0, 1, llx, lly))


def _fit_text(c, text, font, box_w, size):
    while size > 5 and pdfmetrics.stringWidth(text, font, size) > box_w:
        size -= 0.5
    while text and pdfmetrics.stringWidth(text, font, size) > box_w:
        text = text[:-1]
    return text, size


def _draw_image(c, png: bytes, x, y, w, h):
    image = ImageReader(io.BytesIO(png))
    iw, ih = image.getSize()
    scale = min(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    c.drawImage(image, x, y + (h - dh) / 2, dw, dh, mask='auto')


def stamp(raw: bytes, ref: str, fields: list[dict], signers: dict[str, dict]) -> bytes:
    """Return the completed PDF: every field drawn on its page and the envelope ID at the top of each page.

    fields: dicts with page, kind, x, y, w, h, value, recipient_id; signers: recipient_id -> {name, signature_png,
    initials_png, signature_id}.
    """
    regular, bold = fonts()
    reader = PdfReader(io.BytesIO(raw), strict=False)
    writer = PdfWriter()
    writer.append(reader)
    by_page: dict[int, list[dict]] = {}
    for f in fields:
        by_page.setdefault(int(f['page']), []).append(f)
    for number, page in enumerate(writer.pages, start=1):
        box = page.mediabox
        rotation = int(page.rotation or 0) % 360
        width, height = float(box.width), float(box.height)
        dw, dh = (height, width) if rotation in (90, 270) else (width, height)
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(dw, dh))
        c.setFillColor(MUTED)
        c.setFont(regular, 6.5)
        c.drawString(14, dh - 11, _safe_text(f'TFSC Envelope ID: {ref}', regular))
        for f in by_page.get(number, []):
            value = f.get('value')
            if value in (None, ''):
                continue
            x, w, h = float(f['x']), float(f['w']), float(f['h'])
            y = dh - float(f['y']) - h
            signer = signers.get(f['recipient_id'], {})
            kind = f['kind']
            if kind == 'signature' and signer.get('signature_png'):
                c.setStrokeColor(ACCENT)
                c.setLineWidth(0.8)
                c.line(x, y + 1, x, y + h - 1)
                c.line(x, y + h - 1, x + 5, y + h - 1)
                c.line(x, y + 1, x + 5, y + 1)
                c.setFillColor(MUTED)
                c.setFont(regular, 5.5)
                c.drawString(x + 4, y + h - 6.5, 'Signed electronically by:')
                c.drawString(x + 4, y + 2, _safe_text(signer.get('signature_id', '')[:16].upper(), regular))
                _draw_image(c, signer['signature_png'], x + 4, y + 8, w - 6, h - 16)
            elif kind == 'initials' and signer.get('initials_png'):
                _draw_image(c, signer['initials_png'], x + 1, y + 1, w - 2, h - 2)
            elif kind == 'note':
                continue
            elif kind == 'radio':
                radius = min(w, h, 13) / 2
                cx, cy = x + radius, y + h / 2
                c.setStrokeColor(INK)
                c.setLineWidth(0.8)
                c.circle(cx, cy, radius - 0.5)
                if value == 'true':
                    c.setFillColor(INK)
                    c.circle(cx, cy, radius * 0.5, stroke=0, fill=1)
            elif kind == 'checkbox':
                side = min(w, h, 14)
                c.setStrokeColor(INK)
                c.setLineWidth(0.8)
                c.rect(x, y + (h - side) / 2, side, side)
                if value == 'true':
                    by = y + (h - side) / 2
                    c.line(x + 2.5, by + 2.5, x + side - 2.5, by + side - 2.5)
                    c.line(x + 2.5, by + side - 2.5, x + side - 2.5, by + 2.5)
            else:
                text, size = _fit_text(c, _safe_text(value, regular).replace('\n', ' '), regular, w - 2, min(float(f.get('font_size') or 10.5), h * 0.8))
                c.setFillColor(INK)
                c.setFont(regular, size)
                c.drawString(x + 1, y + (h - size) / 2 + size * 0.22, text)
        c.save()
        overlay = PdfReader(io.BytesIO(buffer.getvalue())).pages[0]
        page.merge_transformed_page(overlay, _display_to_user(rotation, width, height, float(box.left), float(box.bottom)))
    writer.add_metadata({'/Producer': 'TFSC Agreements e-signature', '/Subject': f'Envelope {ref}'})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------ uploads in other formats
OFFICE_SUFFIXES = ('.docx', '.doc', '.odt', '.rtf', '.txt')
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg')


def kind_of(name: str, raw: bytes) -> str:
    """pdf | image | office, judged by the file's content (the name only tells apart formats that share a container)."""
    suffix = Path(name or '').suffix.lower()
    if raw.startswith(b'%PDF'):
        return 'pdf'
    if raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff'):
        return 'image'
    if raw.startswith(b'PK\x03\x04') and suffix in ('.docx', '.odt'):
        return 'office'
    if raw.startswith(b'\xd0\xcf\x11\xe0') and suffix == '.doc':
        return 'office'
    if raw.startswith(b'{\\rtf') and suffix == '.rtf':
        return 'office'
    if suffix == '.txt':
        try:
            raw.decode('utf-8')
            return 'office'
        except UnicodeDecodeError:
            pass
    raise ValueError('Upload a PDF, a Word document (DOCX, DOC, ODT, RTF), a text file or a PNG or JPEG image.')


def image_to_pdf(raw: bytes) -> bytes:
    """Place an image on a Letter page with half-inch margins, scaled to fit and never enlarged beyond 150 dpi."""
    from PIL import Image as PILImage
    try:
        with PILImage.open(io.BytesIO(raw)) as image:
            image.load()
            if image.width * image.height > 40_000_000:
                raise ValueError('The image is too large.')
            image = image.convert('RGB')
            png = io.BytesIO()
            image.save(png, format='PNG')
            width, height = image.size
    except ValueError:
        raise
    except Exception:  # noqa: BLE001  (Pillow raises many kinds of errors for broken images)
        raise ValueError('The image could not be read.') from None
    page_w, page_h = LETTER if height >= width else (LETTER[1], LETTER[0])
    box_w, box_h = page_w - 72, page_h - 72
    scale = min(box_w / width, box_h / height, 72 / 150)
    w, h = width * scale, height * scale
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(page_w, page_h))
    c.drawImage(ImageReader(io.BytesIO(png.getvalue())), (page_w - w) / 2, page_h - 36 - h, w, h)
    c.showPage()
    c.save()
    return out.getvalue()


def office_to_pdf(raw: bytes, suffix: str, timeout: int = 120) -> bytes:
    """Convert a Word, OpenDocument, RTF or text file with LibreOffice (headless, fresh profile, time limit)."""
    import shutil
    tool = shutil.which(os.environ.get('LIBREOFFICE_BIN', 'soffice'))
    if not tool:
        raise ValueError('This installation cannot convert Word documents. Save the document as PDF and upload the PDF.')
    with tempfile.TemporaryDirectory(prefix='tfsc-upload-') as temporary:
        temp = Path(temporary)
        source = temp / ('document' + suffix)
        source.write_bytes(raw)
        try:
            result = subprocess.run([tool, '-env:UserInstallation=' + (temp / 'profile').as_uri(), '--headless', '--norestore', '--convert-to', 'pdf',
                                     '--outdir', str(temp / 'out'), str(source)], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ValueError('The document took too long to convert. Save it as PDF and upload the PDF.') from None
        produced = temp / 'out' / 'document.pdf'
        if result.returncode or not produced.is_file():
            raise ValueError('The document could not be converted. Save it as PDF and upload the PDF.')
        return produced.read_bytes()


def to_pdf(name: str, raw: bytes) -> tuple[bytes, str]:
    """Any supported upload as PDF bytes, with the file name ending in .pdf."""
    kind = kind_of(name, raw)
    stem = Path(name or 'Document').stem or 'Document'
    if kind == 'image':
        pdf = image_to_pdf(raw)
    elif kind == 'office':
        pdf = office_to_pdf(raw, Path(name).suffix.lower())
    else:
        pdf = raw
    inspect(pdf)
    return pdf, stem + '.pdf'


def combine(parts: list[bytes]) -> bytes:
    writer = PdfWriter()
    for part in parts:
        writer.append(PdfReader(io.BytesIO(part), strict=False))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# ------------------------------------------------------------------ certificate of completion
def when(ts: float | None, tz: str) -> str:
    if not ts:
        return ''
    moment = dt.datetime.fromtimestamp(ts, dt.UTC)
    local = moment.astimezone(zoneinfo.ZoneInfo(tz))
    return f"{local.strftime('%b %d, %Y %I:%M:%S %p')} {local.tzname()} ({moment.strftime('%Y-%m-%d %H:%M:%S')} UTC)"


def certificate(env: dict, entity: dict, docs: list[dict], recipients: list[dict], events: list[dict], sender: dict, ersd: str, origin: str) -> bytes:
    regular, bold = fonts()
    tz = entity['timezone']
    styles = {
        'title': ParagraphStyle('t', fontName=bold, fontSize=17, leading=21, textColor=INK, spaceAfter=4),
        'h': ParagraphStyle('h', fontName=bold, fontSize=10.5, leading=13, textColor=INK, spaceBefore=12, spaceAfter=5),
        'p': ParagraphStyle('p', fontName=regular, fontSize=8, leading=10.5, textColor=colors.black, alignment=TA_LEFT),
        'small': ParagraphStyle('s', fontName=regular, fontSize=7, leading=9, textColor=MUTED),
        'cell': ParagraphStyle('c', fontName=regular, fontSize=7.6, leading=9.6),
        'cellb': ParagraphStyle('cb', fontName=bold, fontSize=7.6, leading=9.6),
    }

    def p(text, style='cell'):
        from xml.sax.saxutils import escape
        return Paragraph(escape(_safe_text(str(text), regular)).replace('\n', '<br/>'), styles[style])

    def table(rows, widths, header=True):
        t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
        style = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#D5DAE3')),
                 ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4)]
        if header:
            style.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EEF2F8')))
        t.setStyle(TableStyle(style))
        return t

    story = []
    logo = Path(__file__).resolve().parent / 'assets' / 'brand' / entity.get('logo', '')
    if entity.get('logo') and logo.is_file():
        lw, lh = ImageReader(str(logo)).getSize()
        story += [Image(str(logo), lw * 34 / lh, 34, hAlign='LEFT'), Spacer(1, 8)]
    story += [p('Certificate of Completion', 'title'),
              p(f"{entity['name']} · issued by TFSC Agreements e-signature ({origin})", 'small'), Spacer(1, 8)]
    status = env['status'].capitalize()
    story.append(table([
        [p('Envelope ID', 'cellb'), p(env['ref']), p('Status', 'cellb'), p(status)],
        [p('Subject', 'cellb'), p(env['subject']), p('Envelope originator', 'cellb'), p(f"{sender['name']}\n{sender['email']}")],
        [p('Documents', 'cellb'), p(str(len(docs))), p('Pages', 'cellb'), p(str(sum(d['pages'] for d in docs)))],
        [p('Created', 'cellb'), p(when(env['created'], tz)), p('Sent', 'cellb'), p(when(env['sent_at'], tz))],
        [p('Completed', 'cellb'), p(when(env.get('completed_at'), tz)), p('Time zone', 'cellb'), p(tz)],
    ], [80, 175, 85, 180], header=False))

    story.append(p('Documents', 'h'))
    rows = [[p('Document', 'cellb'), p('Pages', 'cellb'), p('SHA-256 as sent', 'cellb'), p('SHA-256 completed', 'cellb')]]
    for d in docs:
        rows.append([p(d['name']), p(str(d['pages'])), p(d['sha256']), p(d.get('final_sha256') or '')])
    story.append(table(rows, [130, 35, 177, 178]))

    story.append(p('Recipient events', 'h'))
    for r in recipients:
        role = {'signer': 'Signer', 'in_person': 'Signer (in person)', 'viewer': 'Needs to view', 'cc': 'Receives a copy'}[r['role']]
        if r.get('status') == 'reassigned':
            role += f" · assigned to {r.get('reassigned_name') or 'someone else'}"
        left = [p(f"{r['name']}\n{r['email']}".strip(), 'cellb'), p(f"{role} · routing order {r['routing_order']}"),
                p(f"Security level: email link{', access code' if r.get('access_code') else ''}"
                  + (f"\nAuthentication: {r['auth_method']}" if r.get('auth_method') else ''))]
        if r.get('signature_png'):
            image = ImageReader(io.BytesIO(r['signature_png']))
            iw, ih = image.getSize()
            scale = min(150 / iw, 40 / ih)
            left += [Image(io.BytesIO(r['signature_png']), iw * scale, ih * scale),
                     p(f"Signature adoption: {(r.get('signature_kind') or '').capitalize()}\nSignature ID: {r.get('signature_id', '')[:32].upper()}")]
        if r.get('ip'):
            left.append(p(f"Using IP address: {r['ip']}"))
        right = [p(line) for line in (
            f"Sent: {when(r.get('sent_at'), tz)}", f"Viewed: {when(r.get('delivered_at'), tz)}",
            f"Electronic record and signature disclosure accepted: {when(r.get('consent_at'), tz)}",
            (f"Signed: {when(r.get('signed_at'), tz)}" if r['role'] in ('signer', 'in_person') else f"Completed: {when(r.get('signed_at'), tz)}"),
            (f"Declined: {when(r.get('declined_at'), tz)} · reason: {r.get('decline_reason')}" if r.get('declined_at') else ''),
        ) if line and line.split(': ', 1)[1]]
        story.append(KeepTogether(table([[left, right]], [260, 260], header=False)))
        story.append(Spacer(1, 4))

    story.append(p('Audit trail', 'h'))
    story.append(p('Each event is chained to the previous one with SHA-256, so a changed or removed event breaks the chain.', 'small'))
    rows = [[p('Time', 'cellb'), p('Event', 'cellb'), p('By', 'cellb'), p('IP address', 'cellb'), p('Event hash', 'cellb')]]
    for e in events:
        rows.append([p(when(e['at'], tz)), p(e['label']), p(e['actor']), p(e['ip']), p(e['hash'][:20] + '…')])
    story.append(table(rows, [118, 150, 120, 62, 70]))

    story.append(p('Electronic record and signature disclosure', 'h'))
    for para in ersd.split('\n\n')[1:]:  # the first paragraph is the title, already shown as the heading
        story.append(p(para, 'p'))
        story.append(Spacer(1, 3))

    out = io.BytesIO()

    def footer(c, document):
        c.saveState()
        c.setFont(regular, 7)
        c.setFillColor(MUTED)
        c.drawString(0.6 * inch, 0.45 * inch, f"Envelope {env['ref']} · Certificate of Completion")
        c.drawRightString(LETTER[0] - 0.6 * inch, 0.45 * inch, f'Page {document.page}')
        c.restoreState()
    doc = SimpleDocTemplate(out, pagesize=LETTER, leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.7 * inch,
                            title=f"Certificate of Completion {env['ref']}", author=entity['name'])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
