"""Company identity is selected from this registry, never free-form input."""
from pathlib import Path
APP=Path(__file__).resolve().parent
ENTITIES={
 'SCRIBE ALIGN LLC':dict(key='scribe',name='SCRIBE ALIGN LLC',short='Scribe Align',display='Scribe Align LLC',dark='#000F18',signatory='Naeem Abbas',title='Chief Executive Officer',governing_state='Nevada',timezone='America/Los_Angeles',logo='scribe.png',color='#004B79',accent='#00AEEF',signature='Naeem Abbas.png',template='letterhead.docx',sha256='cef540f5dda4700eae10ebaf86d8707b67b814c90ea5ad33264142cc34e2e895',address='9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134',email='',width=10368,top=1872,bottom=1512,left=936,right=936),
 'EXTENDED HEALTH SERVICES INC.':dict(key='ehs',name='EXTENDED HEALTH SERVICES INC.',short='Extended Health Services',display='Extended Health Services Inc.',dark='#101A24',signatory='Naeem Abbas',title='Chief Executive Officer',governing_state='Missouri',timezone='America/Chicago',logo='ehs.png',color='#1C2A39',accent='#FBCD35',signature='Naeem Abbas.png',template='letterhead-ehs.docx',sha256='2adb13f35edd41a18c850d6fb7cb0500cb2e60b6376a4ee46060104955d06b5b',address='105 Russel St, Hayti, MO 63851',email='info@extendedhealthservices.us',width=9590,top=1900,bottom=1800,left=1158,right=1158),
 '24-7 CONSULTANCY LLC':dict(key='24-7',name='24-7 CONSULTANCY LLC',short='24-7 Consultancy',display='24-7 Consultancy LLC',dark='#0F2747',signatory='Danish Ather',title='Chief Operating Officer',governing_state='Georgia',timezone='America/New_York',logo='24-7.png',color='#1D4277',accent='#189BD2',signature='Danish Ather.png',template='letterhead-24-7.docx',sha256='697dcf72bda5dafeb393b1bc86e01ad14add1b866acd2a316e584bb1b6c19cc4',address='1645 Metropolitan Blvd, Tallahassee, FL 32308',email='info@24-7consultancyllc.com',width=10368,top=2050,bottom=1800,left=936,right=936),
}
DEFAULT_ENTITY='SCRIBE ALIGN LLC'
def get_entity(name=None):
    if name is None or name=='': name=DEFAULT_ENTITY
    if name not in ENTITIES: raise ValueError('Select a supported contracting company.')
    return ENTITIES[name]

def signature_path(data_dir,entity):
    return Path(data_dir)/'signatures'/get_entity(entity)['signature']
