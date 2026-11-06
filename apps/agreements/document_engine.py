"""Scribe Align contract documents using the retained letterhead and native Word.

The DOCX package is patched narrowly: header/footer artwork, anchors, source
styles and media are preserved. Word is the common pagination engine for both
editable documents and PDFs. No remote service receives contract information.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from zipfile import ZipFile
import xml.etree.ElementTree as ET
from signature_store import MAX_HEIGHT, MAX_WIDTH, image_xml
from entities import get_entity

APP = Path(__file__).resolve().parent
TEMPLATE = APP / 'assets' / 'letterhead.docx'
TEMPLATE_SHA256 = 'cef540f5dda4700eae10ebaf86d8707b67b814c90ea5ad33264142cc34e2e895'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
ACCENT = '004B79'
WIDTH = 10368
_WORD_LOCK = threading.Lock()


def _text(value):
    s = str(value or '').replace('\r\n', '\n').replace('\r', '\n')
    return ''.join(c for c in s if ord(c) >= 32 or c in '\n\t')


def _escape(value):
    return html.escape(_text(value), quote=True)


def _run(text, bold=False, size=None, color=None):
    props = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
    if bold:
        props += '<w:b/>'
    if size:
        props += f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
    if color:
        props += f'<w:color w:val="{color}"/>'
    chunks = _text(text).split('\n')
    return '<w:r><w:rPr>' + props + '</w:rPr>' + '<w:br/>'.join('<w:t xml:space="preserve">'+_escape(c)+'</w:t>' for c in chunks) + '</w:r>'


def _p(text, style='SABody', *, keep=False, page_break=False, bold=False, align=None, bullet=False, number=None):
    props = f'<w:pStyle w:val="{style}"/>'
    if keep:
        props += '<w:keepNext/>'
    if page_break:
        props += '<w:pageBreakBefore/>'
    if align:
        props += f'<w:jc w:val="{align}"/>'
    if bullet:
        props += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="9999"/></w:numPr>'
    if number:
        props += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{int(number)}"/></w:numPr>'
    return '<w:p><w:pPr>'+props+'</w:pPr>'+_run(text,bold)+'</w:p>'


# Numbered and lettered lists (format_guard): one numbering instance per list, so each list restarts at its own first
# number; the marker hangs like the bullets'. Ids sit far above any a letterhead's own numbering uses.
LIST_BASE = 10000
_LIST_ABSTRACT = {'decimal': (9998, 'decimal', '%1.', 400, 280), 'letter': (9997, 'lowerLetter', '(%1)', 440, 320)}


def _list_numbering(lists):
    abstract = ''.join(f'<w:abstractNum w:abstractNumId="{aid}"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/>'
                       f'<w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="{left}"/></w:tabs>'
                       f'<w:ind w:left="{left}" w:hanging="{hanging}"/></w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/></w:rPr></w:lvl></w:abstractNum>'
                       for aid, fmt, text, left, hanging in _LIST_ABSTRACT.values()) if lists else ''
    nums = ''.join(f'<w:num w:numId="{LIST_BASE+index}"><w:abstractNumId w:val="{_LIST_ABSTRACT[style][0]}"/><w:lvlOverride w:ilvl="0">'
                   f'<w:startOverride w:val="{start}"/></w:lvlOverride></w:num>' for index, (style, start) in enumerate(lists, 1))
    return abstract, nums


def _label_runs(block):
    """A list item that starts with a short label ("System Access: Provide ...") prints the label in bold."""
    label, text = block.get('label'), _text(block.get('text', ''))
    if block.get('type') in ('bullet', 'number') and label and text.startswith(label) and len(text) > len(label):
        return [{'text': label, 'bold': True}, {'text': text[len(label):], 'bold': False}]
    return None


def _style(style_id, name, size=22, bold=False, color='000000', before=0, after=120, align='both', keep=False, outline=None, line=269):
    p = f'<w:spacing w:before="{before}" w:after="{after}" w:line="{line}" w:lineRule="auto"/><w:jc w:val="{align}"/><w:widowControl/><w:contextualSpacing w:val="0"/>'
    if keep:
        p += '<w:keepNext/><w:keepLines/>'
    if outline is not None:
        p += f'<w:outlineLvl w:val="{outline}"/>'
    return f'<w:style w:type="paragraph" w:customStyle="1" w:styleId="{style_id}"><w:name w:val="{name}"/><w:next w:val="SABody"/><w:qFormat/><w:pPr>{p}</w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>{"<w:b/>" if bold else ""}<w:color w:val="{color}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/><w:lang w:val="en-US"/></w:rPr></w:style>'


def _table(headers, rows, signature=False, width=WIDTH):
    if not headers or any(len(r) != len(headers) for r in rows):
        raise ValueError('Each pricing row must match the table headings.')
    count = len(headers)
    widths = [int(width * .58), width-int(width * .58)] if count == 2 and not signature else [width//count]*count
    widths[-1] = width-sum(widths[:-1])
    border = ''.join(f'<w:{side} w:val="{"nil" if signature else "single"}" w:sz="4" w:color="D9D9D9"/>' for side in ('top','left','bottom','right','insideH','insideV'))
    table = f'<w:tbl><w:tblPr><w:tblW w:w="{width}" w:type="dxa"/><w:jc w:val="center"/><w:tblBorders>{border}</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="110" w:type="dxa"/><w:left w:w="130" w:type="dxa"/><w:bottom w:w="110" w:type="dxa"/><w:right w:w="130" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>' + ''.join(f'<w:gridCol w:w="{x}"/>' for x in widths) + '</w:tblGrid>'
    for i,row in enumerate([headers]+list(rows)):
        table += '<w:tr><w:trPr><w:cantSplit/>'+ ('<w:tblHeader/>' if i==0 and not signature else '')+'</w:trPr>'
        for j,cell in enumerate(row):
            fill = 'EAF2F7' if i==0 else ('F7FAFC' if i%2==0 else 'FFFFFF')
            table += f'<w:tc><w:tcPr><w:tcW w:w="{widths[j]}" w:type="dxa"/>' + ('' if signature else f'<w:shd w:val="clear" w:fill="{fill}"/>') + '<w:vAlign w:val="center"/></w:tcPr>'
            table += _p(cell,'SASignature' if signature else 'SATable',bold=i==0,keep=signature and i<len(rows),align='left')+'</w:tc>'
        table += '</w:tr>'
    return table+'</w:tbl>'


def _signature(text, data, contract_id, final_clause='', source_exact=False, entity=None):
    entity=entity or get_entity(data.get('entity'))
    client = _text(data.get('client_name'))
    date = _text(data.get('effective_date'))
    try:
        date = dt.date.fromisoformat(date).strftime('%B %d, %Y').replace(' 0',' ')
    except ValueError:
        pass
    body = _p('Final Terms and Execution','SAHeading1',page_break=True,keep=True)
    body += final_clause
    body += _p(f'Agreement reference: {contract_id}\nClient: {client}\nEffective date: {date}','SAMeta',keep=True)
    body += _p(text,keep=True)
    company_signature = 'Signature: __________________________'
    body += _table([entity['name'],client], [
        [company_signature,'Signature: __________________________'],
        ['Name: '+_text(data.get('_signatory') or entity['signatory']),'Name: '+(_text(data.get('client_signatory')) or '____________________________')],
        ['Title: '+_text(data.get('_signatory_title') or entity['title']),'Title: '+(_text(data.get('client_title')) or '_____________________________')],
        # The company signs as of the effective date; the client's line is dated when the client signs.
        ['Date: '+date,'Date: ______________________________'],
    ],signature=True,width=entity['width'])
    signing_p=_p(company_signature,'SASignature',keep=True,align='left')
    line='<w:p><w:pPr><w:pStyle w:val="SASignature"/><w:keepNext/><w:spacing w:before="1180" w:after="140"/>'+f'<w:tabs><w:tab w:val="left" w:leader="underscore" w:pos="{entity["width"]//2-350}"/></w:tabs></w:pPr>'+_run('Signature: ')+'<w:r><w:tab/></w:r>'
    body=body.replace(signing_p,line+data.get('_signature_xml','')+'</w:p>',1)
    body=body.replace(signing_p,line+'</w:p>',1)
    if not source_exact:
        body += _p('The persons signing above represent that they are authorized to bind the Party identified beside their signature. Counterparts and electronic signatures may be used as provided in this Agreement.','SAMeta')
    return body


def _write_package(path, parts, infos):
    path = Path(path)
    temporary = path.with_name(path.name+'.writing')
    try:
        with ZipFile(temporary,'w') as z:
            for info in infos:
                z.writestr(info,parts.pop(info.filename))
            for name,content in parts.items():
                z.writestr(name,content)
        os.replace(temporary,path)
    finally:
        if temporary.exists():
            temporary.unlink()


TEST_WATERMARK = 'TEST · NOT A VALID AGREEMENT'
_VML_NS = {'v': 'urn:schemas-microsoft-com:vml', 'o': 'urn:schemas-microsoft-com:office:office', 'w10': 'urn:schemas-microsoft-com:office:word'}
_R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _watermark_paragraph(number):
    """A light diagonal text watermark (Word's own watermark shape), centred on the page behind the text."""
    shape = (f'<v:shape id="PowerPlusWaterMarkObject{number}" o:spid="_x0000_s{4090 + number}" type="#_x0000_t136" '
             'style="position:absolute;margin-left:0;margin-top:0;width:520pt;height:46pt;rotation:315;z-index:-251651072;'
             'mso-position-horizontal:center;mso-position-horizontal-relative:margin;mso-position-vertical:center;mso-position-vertical-relative:margin" '
             'o:allowincell="f" fillcolor="#e9a23b" stroked="f"><v:fill opacity=".38"/>'
             f'<v:textpath style="font-family:&quot;Arial&quot;;font-size:1pt;font-weight:bold" string="{_escape(TEST_WATERMARK)}"/>'
             '<w10:wrap anchorx="margin" anchory="margin"/></v:shape>')
    shapetype = ('<v:shapetype id="_x0000_t136" coordsize="21600,21600" o:spt="136" adj="10800" path="m@7,l@8,m@5,21600l@6,21600e">'
                 '<v:formulas><v:f eqn="sum #0 0 10800"/><v:f eqn="prod #0 2 1"/><v:f eqn="sum 21600 0 @1"/><v:f eqn="sum 0 0 @2"/>'
                 '<v:f eqn="sum 21600 0 @3"/><v:f eqn="if @0 @3 0"/><v:f eqn="if @0 21600 @1"/><v:f eqn="if @0 0 @2"/><v:f eqn="if @0 @4 21600"/>'
                 '<v:f eqn="mid @5 @6"/><v:f eqn="mid @8 @5"/><v:f eqn="mid @7 @8"/><v:f eqn="mid @6 @7"/><v:f eqn="sum @6 0 @5"/></v:formulas>'
                 '<v:path textpathok="t" o:connecttype="custom" o:connectlocs="@9,0;@10,10800;@11,21600;@12,10800" o:connectangles="270,180,90,0"/>'
                 '<v:textpath on="t" fitshape="t"/><v:handles><v:h position="#0,bottomRight" xrange="6629,14971"/></v:handles>'
                 '<o:lock v:ext="edit" text="t" shapetype="t"/></v:shapetype>')
    # An exact, hair-thin line so the header (and so the page layout) keeps its height.
    return ('<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/><w:rPr><w:sz w:val="2"/></w:rPr></w:pPr>'
            f'<w:r><w:rPr><w:noProof/><w:sz w:val="2"/></w:rPr><w:pict>{shapetype}{shape}</w:pict></w:r></w:p>')


def _with_namespaces(root_tag, names):
    for prefix in names:
        if f'xmlns:{prefix}=' not in root_tag:
            uri = _VML_NS.get(prefix) or (_R_NS if prefix == 'r' else W)
            root_tag = root_tag[:-1] + f' xmlns:{prefix}="{uri}">'
    return root_tag


def add_test_watermark(parts, section):
    """Put the TEST watermark in every header the document can show, so it is on every page. Returns the new section."""
    number = 0
    for name in sorted(parts):
        if re.fullmatch(r'word/header\d+\.xml', name):
            number += 1
            body = parts[name].decode('utf-8')
            body = re.sub(r'<w:hdr\b[^>]*>', lambda m: _with_namespaces(m.group(0), ('v', 'o', 'w10')), body, count=1)
            parts[name] = body.replace('</w:hdr>', _watermark_paragraph(number) + '</w:hdr>').encode('utf-8')
    # Pages whose header type has no header part (a letterhead without headers, a first or even page without its own)
    # get a header that holds only the watermark.
    needed = ['default']
    if '<w:titlePg' in section and not re.search(r'<w:titlePg\b[^>]*w:val="(0|false)"', section):
        needed.append('first')
    if re.search(rb'<w:evenAndOddHeaders\b(?![^>]*w:val="(0|false)")', parts.get('word/settings.xml', b'')):
        needed.append('even')
    missing = [kind for kind in needed if not re.search(fr'<w:headerReference\b[^>]*w:type="{kind}"', section)]
    if missing:
        number += 1
        parts['word/header-sa-test.xml'] = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:hdr xmlns:w="{W}" xmlns:r="{_R_NS}" '
                                            + ' '.join(f'xmlns:{k}="{v}"' for k, v in _VML_NS.items()) + '>'
                                            + _watermark_paragraph(number) + '</w:hdr>').encode('utf-8')
        rel = f'<Relationship Id="rIdSATestHeader" Type="{_R_NS}/header" Target="header-sa-test.xml"/>'
        parts['word/_rels/document.xml.rels'] = parts['word/_rels/document.xml.rels'].replace(b'</Relationships>', rel.encode() + b'</Relationships>')
        override = '<Override PartName="/word/header-sa-test.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        parts['[Content_Types].xml'] = parts['[Content_Types].xml'].replace(b'</Types>', override.encode() + b'</Types>')
        references = ''.join(f'<w:headerReference w:type="{kind}" r:id="rIdSATestHeader"/>' for kind in missing)
        section = re.sub(r'(<w:sectPr\b[^>]*>)', lambda m: m.group(1) + references, section, count=1)
    return section


def generate_docx(blocks, data, contract_id, path, entity=None):
    """Create editable DOCX without touching the retained letterhead."""
    entity=entity or get_entity(data.get('entity'))
    template=Path(entity['template_path'])
    if hashlib.sha256(template.read_bytes()).hexdigest() != entity['sha256']:
        raise RuntimeError('The retained letterhead changed. Restore the company letterhead or have the layout revalidated before generating contracts.')
    if not blocks:
        raise ValueError('The agreement has no clauses.')
    with ZipFile(template) as z:
        infos=z.infolist()
        parts={i.filename:z.read(i.filename) for i in infos}
    data=dict(data)
    if data.get('_signature_bytes') or data.get('_signature_path'):
        raw=data.get('_signature_bytes') or Path(data['_signature_path']).read_bytes()
        # The signature starts 780000 EMU after the column edge; it never runs past the end of the signature rule. Its size is
        # the company's signature size (Company settings) as a share of the largest box.
        scale=min(1.0,max(0.3,float(data.get('_signature_scale') or 1.0)))
        width=min(MAX_WIDTH,(entity['width']//2-350)*635-780000-40000)
        ext,drawing=image_xml(raw,data.get('_signature_crop'),max_width=int(width*scale),max_height=int(MAX_HEIGHT*scale))
        data['_signature_xml']=drawing
        parts['word/media/sa-signature.'+ext]=raw
        rel=f'<Relationship Id="rIdSASignature" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/sa-signature.{ext}"/>'
        parts['word/_rels/document.xml.rels']=parts['word/_rels/document.xml.rels'].replace(b'</Relationships>',rel.encode()+b'</Relationships>')
        types=parts['[Content_Types].xml']
        if not re.search(fr'Extension="{ext}"'.encode(),types):
            content_type='image/png' if ext=='png' else 'image/jpeg'
            types=types.replace(b'</Types>',f'<Default Extension="{ext}" ContentType="{content_type}"/></Types>'.encode())
        parts['[Content_Types].xml']=types
    xml=parts['word/document.xml'].decode('utf-8')
    section=re.search(r'<w:sectPr\b[^>]*>.*?</w:sectPr>',xml,re.S).group(0)
    for margin in ('top','right','bottom','left'):
        section=re.sub(fr'w:{margin}="\d+"',f'w:{margin}="{entity[margin]}"',section)
    if data.get('test'):
        section=add_test_watermark(parts,section)
    if entity['key']!='scribe':
        page_height=int(re.search(r'w:pgSz[^>]*w:h="(\d+)"',section).group(1))
        page_field='<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'+_run('1',size=18)+'<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        total_field=page_field.replace(' PAGE ',' NUMPAGES ')
        # An added company's bottom margin comes from its letterhead check (footer artwork + room for this line), so the
        # counter sits just below the text, above the artwork. The three built-in letterheads keep their tested place.
        custom=bool(entity.get('custom'))
        counter_y=page_height-entity['bottom']+150 if custom else page_height-1500
        numbers='<w:p><w:pPr><w:framePr w:w="2500" w:h="260" w:hRule="atLeast" w:x="'+str(entity['left']+entity['width']-2500)+'" w:y="'+str(counter_y)+'" w:hAnchor="page" w:vAnchor="page" w:wrap="none"/><w:jc w:val="right"/></w:pPr>'+_run('Page ',size=18)+page_field+_run(' of ',size=18)+total_field+'</w:p>'
        server_renderer=os.environ.get('TFSC_RENDERER')=='libreoffice'
        if server_renderer:
            # Writer ignores page-relative footer frames. A regular paragraph
            # before the retained artwork keeps the counter in clear white space;
            # room after it lifts it above a tall footer (the footer grows upwards).
            lift=max(0,entity['bottom']-1500) if custom else 0
            numbers=re.sub(r'<w:framePr[^>]*/>',f'<w:spacing w:before="0" w:after="{lift}" w:line="240" w:lineRule="auto"/>',numbers)
        for name in list(parts):
            if re.fullmatch(r'word/footer\d+\.xml',name):
                if server_renderer:
                    parts[name]=re.sub(rb'(<w:ftr\b[^>]*>)',lambda m:m[1]+numbers.encode(),parts[name],count=1)
                else:parts[name]=parts[name].replace(b'</w:ftr>',numbers.encode()+b'</w:ftr>')
            if entity['key']=='24-7' and re.fullmatch(r'word/header\d+\.xml',name):
                parts[name]=parts[name].replace(b'<w:t>0</w:t>',b'<w:t></w:t>')
    content=[]
    signature_count=0
    lists=[]            # (style, start) of each numbered list, in order
    previous=None
    for block in blocks:
        kind=block.get('type')
        text=block.get('text','')
        if kind=='title':
            content.append(_p(text,'SATitle',keep=True))
        elif kind in ('h1','h2','h3'):
            content.append(_p(text,'SAHeading'+kind[-1],keep=True))
        elif kind in ('p','bullet','number','lead'):
            number=None
            if kind=='number':
                style='letter' if block.get('style')=='letter' else 'decimal'
                if block.get('start') is not None or not previous or previous.get('type')!='number' or ('letter' if previous.get('style')=='letter' else 'decimal')!=style:
                    lists.append((style,max(1,int(block.get('start') or 1))))
                number=LIST_BASE+len(lists)
            # A lead-in ("The Client shall:") is left-aligned and stays with the list it introduces.
            paragraph=_p(text,bullet=kind=='bullet',number=number,align='left' if kind=='lead' else None,keep=kind=='lead')
            runs=block.get('runs') or _label_runs(block)
            if runs:
                paragraph=paragraph.replace(_run(text),''.join(_run(r['text'],r.get('bold',False)) for r in runs))
            content.append(paragraph)
        elif kind=='table':
            content.append(_table(block.get('headers',[]),block.get('rows',[]),width=entity['width']))
            content.append(_p('','SASpacer'))
        elif kind=='signature':
            signature_count+=1
            # Keep the final substantive clause on the execution page. This
            # prevents a two-line tail page followed by a detached signature page.
            final_clause = content.pop() if content else ''
            if content and re.search(r'w:val="SAHeading[23]"', content[-1]):
                final_clause = content.pop() + final_clause
            content.append(_signature(text,data,contract_id,final_clause,block.get('source_exact',False),entity=entity))
        else:
            raise ValueError(f'Unsupported document block: {kind}')
        previous=block
    if signature_count!=1:
        raise ValueError('Each agreement must include exactly one execution block.')
    xml=re.sub(r'<w:body>.*?</w:body>',lambda _: '<w:body>'+''.join(content)+section+'</w:body>',xml,flags=re.S)
    parts['word/document.xml']=xml.encode('utf-8')
    styles=[_style('SABody','Scribe Align Body'),
            _style('SATitle','Scribe Align Title',32,True,ACCENT,after=160,align='center',keep=True),
            _style('SAMeta','Scribe Align Reference',20,after=120,align='left'),
            _style('SATable','Scribe Align Table',21,after=40,align='left',line=250),
            _style('SASignature','Scribe Align Signature',22,after=140,align='left'),
            _style('SASpacer','Scribe Align Spacer',4,after=40,line=240)]
    for level,size in ((1,32),(2,28),(3,26)):
        styles.append(_style('SAHeading'+str(level),'Scribe Align Heading '+str(level),size,True,ACCENT,180 if level==1 else 120,80,'left',True,level-1))
    parts['word/styles.xml']=parts['word/styles.xml'].replace(b'</w:styles>',''.join(styles).encode()+b'</w:styles>')
    list_abstract,list_nums=_list_numbering(lists)
    numbering='<w:abstractNum w:abstractNumId="9999"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="360"/></w:tabs><w:ind w:left="360" w:hanging="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/></w:rPr></w:lvl></w:abstractNum>'+list_abstract+'<w:num w:numId="9999"><w:abstractNumId w:val="9999"/></w:num>'+list_nums
    if 'word/numbering.xml' in parts:
        parts['word/numbering.xml']=parts['word/numbering.xml'].replace(b'</w:numbering>',numbering.encode()+b'</w:numbering>')
    elif any(b.get('type') in ('bullet','number') for b in blocks):
        parts['word/numbering.xml']=('<w:numbering xmlns:w="'+W+'">'+numbering+'</w:numbering>').encode()
        parts['word/_rels/document.xml.rels']=parts['word/_rels/document.xml.rels'].replace(b'</Relationships>',b'<Relationship Id="rIdSANumbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>')
        parts['[Content_Types].xml']=parts['[Content_Types].xml'].replace(b'</Types>',b'<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/></Types>')
    settings=parts['word/settings.xml'].decode()
    settings=re.sub(r'<w:updateFields\b[^>]*/>','',settings)
    settings=settings.replace('</w:settings>','<w:updateFields w:val="true"/></w:settings>')
    parts['word/settings.xml']=settings.encode()
    if 'docProps/core.xml' in parts:
        core=parts['docProps/core.xml'].decode()
        for tag in ('dc:creator','cp:lastModifiedBy'):
            core=re.sub(fr'<{tag}>.*?</{tag}>',f'<{tag}>{_escape(entity["name"])}</{tag}>',core,flags=re.S)
        parts['docProps/core.xml']=core.encode()
    for name in ('word/document.xml','word/styles.xml','word/settings.xml','word/numbering.xml',*[n for n in parts if re.fullmatch(r'word/header[\w-]*\.xml',n)]):
        if name in parts:
            ET.fromstring(parts[name])
    destination=Path(path).resolve()
    destination.parent.mkdir(parents=True,exist_ok=True)
    _write_package(destination,parts,infos)
    return destination


def _refresh_field_cache(path, pages):
    """Set footer caches without resaving and rewriting the original artwork."""
    with ZipFile(path) as z:
        infos=z.infolist()
        parts={i.filename:z.read(i.filename) for i in infos}
    for name,body in list(parts.items()):
        if re.fullmatch(r'word/footer\d+\.xml',name):
            xml=body.decode()
            for field,cache in [('PAGE',1),('NUMPAGES',pages)]:
                pattern=r'(<w:instrText\b[^>]*>\s*'+field+r'\s*</w:instrText>.*?<w:fldChar w:fldCharType="separate"/>.*?<w:t\b[^>]*>)([^<]*)(</w:t>)'
                xml=re.sub(pattern,lambda m:m.group(1)+str(cache)+m.group(3),xml,flags=re.S)
            parts[name]=xml.encode()
    _write_package(path,parts,infos)


def convert_to_pdf(docx_path, pdf_path, timeout=120):
    """Export in hidden Word. Raise actionable error; never return partial output."""
    docx=Path(docx_path).resolve()
    pdf=Path(pdf_path).resolve()
    pdf.parent.mkdir(parents=True,exist_ok=True)
    if os.environ.get('TFSC_RENDERER','word')=='libreoffice':
        import tempfile,shutil
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory(prefix='tfsc-render-') as temporary:
            temp=Path(temporary);profile=(temp/'profile').as_uri()
            tool=os.environ.get('LIBREOFFICE_BIN','soffice')
            result=subprocess.run([tool,'-env:UserInstallation='+profile,'--headless','--convert-to','pdf','--outdir',str(temp),str(docx)],capture_output=True,timeout=timeout)
            produced=temp/(docx.stem+'.pdf')
            if result.returncode or not produced.is_file():raise RuntimeError('The server document renderer could not export the PDF.')
            pages=len(PdfReader(produced).pages)
            if pages<1:raise RuntimeError('The exported PDF has no pages.')
            shutil.copy2(produced,pdf);_refresh_field_cache(docx,pages)
        return pdf
    powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    with _WORD_LOCK:
        try:
            result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(APP/'export_word.ps1'),'-DocxPath',str(docx),'-PdfPath',str(pdf)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,creationflags=flags)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Microsoft Word did not complete PDF export within two minutes. Open Word normally, finish any activation or recovery dialog, close that dialog, and retry. The client package has not been published.') from exc
        if result.returncode or not pdf.exists() or pdf.stat().st_size<1000:
            detail=(result.stderr or result.stdout).strip()[-1600:]
            raise RuntimeError('Microsoft Word could not export this agreement. Confirm desktop Word is installed and activated and dismiss any Word dialogs. '+detail)
        try:
            metadata=json.loads(result.stdout.strip().splitlines()[-1])
            pages=int(metadata['pages'])
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            raise RuntimeError('Word exported a PDF but did not confirm its page count. Please retry.') from exc
        _refresh_field_cache(docx,pages)
    return pdf
