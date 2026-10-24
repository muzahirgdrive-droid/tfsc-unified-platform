"""Local-only signature image validation. Original image bytes are retained."""
import base64
import struct

def image_info(body):
    if body.startswith(b'\x89PNG\r\n\x1a\n') and len(body)>24 and body[12:16]==b'IHDR':
        width,height=struct.unpack('>II',body[16:24]); ext='png'
    elif body.startswith(b'\xff\xd8'):
        pos=2; width=height=0; ext='jpg'
        while pos+4<len(body):
            if body[pos]!=255: break
            marker=body[pos+1]; pos+=2
            if marker in (0xD9,0xDA): break
            if marker==255: pos-=1; continue
            size=int.from_bytes(body[pos:pos+2],'big')
            if size<2: break
            if marker in (0xC0,0xC1,0xC2):
                height,width=struct.unpack('>HH',body[pos+3:pos+7]); break
            pos+=size
    else:
        raise ValueError('Choose a PNG or JPEG signature image.')
    if not 1<=width<=12000 or not 1<=height<=12000:
        raise ValueError('The signature image has invalid dimensions.')
    return ext,width,height

def save_signature(folder, value):
    try: body=base64.b64decode(value,validate=True)
    except Exception: raise ValueError('The signature image could not be read.') from None
    if not body or len(body)>2_000_000: raise ValueError('Choose a signature image smaller than 2 MB.')
    image_info(body)
    folder.mkdir(parents=True,exist_ok=True)
    destination=folder/'company-signature.image'
    temporary=folder/'company-signature.tmp'
    temporary.write_bytes(body); temporary.replace(destination)

def image_xml(body, crop=None, max_width=2103120, max_height=658368, baseline=686600):
    # Sizes in EMU (914400 per inch): at most 2.3 x 0.72 in, its lower edge on the signature rule (baseline below the
    # paragraph top), so a trimmed signature reads clearly on the line.
    ext,width,height=image_info(body)
    crop=crop or [0,0,0,0]
    width=width*(1-(crop[0]+crop[2])/100000)
    height=height*(1-(crop[1]+crop[3])/100000)
    scale=min(max_width/width,max_height/height)
    cx,cy=int(width*scale),int(height*scale)
    xml=f'''<w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="9301" name="Authorized company signature"/><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="9302" name="Company signature"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="rIdSASignature"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'''

    xml=xml[xml.index('<w:r>'):xml.rindex('</w:p>')]
    xml=xml.replace('<a:stretch>',f'<a:srcRect l="{crop[0]}" t="{crop[1]}" r="{crop[2]}" b="{crop[3]}"/><a:stretch>')
    xml=xml.replace('<wp:inline ','<wp:anchor simplePos="0" relativeHeight="0" behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1" ')
    # Keep the ink's lower edge on the same baseline as the signature rule as size changes.
    offset=baseline-cy
    xml=xml.replace('<wp:extent', f'<wp:simplePos x="0" y="0"/><wp:positionH relativeFrom="column"><wp:posOffset>780000</wp:posOffset></wp:positionH><wp:positionV relativeFrom="paragraph"><wp:posOffset>{offset}</wp:posOffset></wp:positionV><wp:extent')
    xml=xml.replace('<wp:docPr','<wp:wrapNone/><wp:docPr').replace('</wp:inline>','</wp:anchor>')
    return ext,xml
