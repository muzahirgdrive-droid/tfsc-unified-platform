"""Test helpers for e-signature: a fake SMTP server, synthetic agreement PDFs and signature images."""
import base64
import email
import email.policy
import io
import re
import socketserver
import threading

from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        def say(text):
            self.wfile.write((text + '\r\n').encode())
        say('220 fake.test ESMTP')
        rcpts = []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode(errors='replace').strip()
            upper = command.upper()
            if upper.startswith('EHLO'):
                say('250-fake.test')
                say('250-AUTH PLAIN LOGIN')
                say('250 OK')
            elif upper.startswith('HELO'):
                say('250 fake.test')
            elif upper.startswith('AUTH'):
                self.server.logins.append(command)
                say(self.server.auth_reply)
            elif upper.startswith('MAIL FROM'):
                rcpts = []
                say('250 OK')
            elif upper.startswith('RCPT TO'):
                if self.server.reject_rcpt:
                    say(self.server.reject_rcpt)
                else:
                    rcpts.append(command[8:].strip().strip('<>'))
                    say('250 OK')
            elif upper == 'DATA':
                say('354 End data with <CR><LF>.<CR><LF>')
                lines = []
                while True:
                    data = self.rfile.readline()
                    if data in (b'.\r\n', b'.\n', b''):
                        break
                    lines.append(data[1:] if data.startswith(b'..') else data)
                raw = b''.join(lines)
                self.server.messages.append({'rcpts': rcpts, 'raw': raw, 'msg': email.message_from_bytes(raw, policy=email.policy.default)})
                say('250 queued')
            elif upper == 'QUIT':
                say('221 bye')
                return
            else:
                say('250 OK')


class FakeSMTP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), _Handler)
        self.messages, self.logins = [], []
        self.reject_rcpt = None
        self.auth_reply = '235 Authentication successful'
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server_address[1]

    def stop(self):
        self.shutdown()
        self.server_close()

    def to(self, address):
        return [m for m in self.messages if address in m['rcpts']]

    @staticmethod
    def link(message):
        body = message['msg'].get_body(preferencelist=('plain',)).get_content()
        found = re.search(r'https?://[^\s]+/sign/([A-Za-z0-9_-]{30,60})', body)
        return found.group(1) if found else None


def agreement_pdf(pages=2, client_name_blank=True, rotate=0):
    """A PDF laid out like a generated agreement: body pages, then the two-column signature table."""
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=LETTER)
    for number in range(1, pages):
        c.setFont('Times-Roman', 11)
        c.drawString(54, 700, f'Body text of page {number}. The Parties agree as follows.')
        c.showPage()
    c.setFont('Times-Bold', 16)
    c.drawString(47, 684, 'Final Terms and Execution')
    c.setFont('Times-Roman', 11)
    rows = [('Signature: ______________________', 'Signature: ______________________'), ('Name: Naeem Abbas', 'Name: ____________________' if client_name_blank else 'Name: Jane Example'),
            ('Title: Chief Executive Officer', 'Title: ____________________'), ('Date: October 5, 2026', 'Date: ______________________')]
    y = 451.3
    for left, right in rows:
        c.drawString(53.4, y, left)
        c.drawString(312.6, y, right)
        y -= 32.1
    c.showPage()
    c.save()
    raw = out.getvalue()
    if rotate:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for page in PdfReader(io.BytesIO(raw)).pages:
            page.rotate(rotate)
            writer.add_page(page)
        buffer = io.BytesIO()
        writer.write(buffer)
        raw = buffer.getvalue()
    return raw


def signature_png(text='J. Example', size=(360, 110), solid=False):
    image = Image.new('RGBA', size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    if solid:
        draw.rectangle([0, 0, size[0] - 1, size[1] - 1], fill=(0, 0, 0, 255))
    else:
        draw.line([(10, size[1] - 20), (size[0] - 10, 25)], fill=(20, 30, 90, 255), width=6)
        draw.text((20, 20), text, fill=(20, 30, 90, 255))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()
