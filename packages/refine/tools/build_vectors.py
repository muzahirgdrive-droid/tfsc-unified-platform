"""Regenerates ../vectors.json from the Python reference engine.

Workflow: add inputs here, run this script, REVIEW the diff of vectors.json by eye
(the expected values become the contract both languages must honour), commit.
The owner-mandated vectors in tests/test_required_vectors.py are hand-written and
must never be regenerated.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from tfsc_refine import ENGINE_VERSION, parse_date, parse_tri, refine_email, refine_name, refine_phone, refine_row  # noqa: E402

TODAY = '2026-10-20'
NAMES = [
    "john doe md", "DR. JANE A SMITH, DO", "holistic behavioral health llc", "SUNRISE PEDIATRICS, PA",
    "mary o'neil-mcdonald np", "center for aba therapy of ny inc", "robert king jr dds", "jane q. public, cp",
    "  ALPHA   ortho  &  spine  group  ", "smith", "Dr. john doe md", "First M. Last, MD", "John Smith, Ch",
    "John Smith, Cp", "dr jane smith", "Dr Jane Smith", "JOHN SMITH PA", "john smith pa-c", "Jane Roe, FNP-C",
    "jane roe fnp-bc", "OB/GYN associates of NJ pllc", "McKinsey Health", "DeVito Family Practice",
    "24-7 consultancy llc", "Smith & Co", "Smith & Co.", "acme medical group, inc.", "ACME MEDICAL GROUP INC",
    "acme medical group incorporated", "Bright Smiles Dental PC", "bright smiles dental p.c.", "harbor clinic ltd",
    "Harbor Clinic, Ltd.", "lakeside surgery llp", "lakeside surgery lp", "north shore imaging s.c.",
    "north shore imaging sc", "pacific rehab ps", "Pacific Rehab, P.S.", "smith jones corp", "John Smith PhD",
    "jane doe psyd", "john doe edd", "jane doe pharmd", "mark lee dnp, aprn", "sara kim lcsw", "tom ray lpc",
    "ana diaz lmft", "ana diaz lmhc", "ana diaz lcpc", "ben fox bcba", "ben fox bcba-d", "kim park dpt",
    "kim park pt", "lee wu otr/l", "lee wu ccc-slp", "Dr. Amy Chen, MD, FACS", "amy chen md faap",
    "amy chen md facp", "amy chen md facc", "amy chen md mph", "amy chen md mba", "joe bloggs crna",
    "joe bloggs rn bsn", "joe bloggs msn", "joe bloggs dpm", "joe bloggs dmd", "joe bloggs dc", "joe bloggs od",
    "JAMES O'BRIEN", "james o'brien", "dana d'angelo", "ian macdonald", "ian mackenzie md", "mack smith",
    "machado clinic", "anne-marie smith-jones", "ANNE-MARIE SMITH-JONES NP", "william gates iii",
    "william gates ii md", "henry ford sr.", "henry ford iv", "the center for wellness", "THE CENTER FOR WELLNESS",
    "family care of the valley", "Valley Family Care", "new york ent associates", "la pediatrics",
    "hiv care services", "adhd center of nj", "mri imaging of dc", "icu partners", "er physicians group",
    "rcm solutions llc", "mso partners inc", "ipa network", "aco health partners", "usa medical llc",
    "Dr A / Dr B", "dr smith and dr jones", "Smith; Jones", "\"john doe md\"", "'jane roe'", "John Doe,,",
    "john   doe   md", "JOHN DOE M.D.", "john doe m.d", "John Doe MD.", "Doe, John", "DOE, JOHN",
    "iCare Vision Center", "eHealth solutions llc", "NYU Langone Health", "Kaiser Permanente",
    "kids first pediatrics pa", "Mercy Hospital", "st. mary's hospital", "ST MARY MEDICAL CENTER",
    "advanced chiropractic llc", "smile dental lab", "sunshine pharmacy", "hope counseling services",
    "mind psychiatry pllc", "joint orthopedics", "city surgery center", "metro labs", "elite therapy group",
    "a", "", "   ", "x" * 5, "Dr.", "md", "llc", "John", "Jo Ann Smith", "Mary Ellen O'Connor",
    "Juan de la Cruz", "VAN DER BERG CLINIC", "Lucia Di Marco", "José García, MD", "ZOË ADAMS, NP",
    "Nguyễn Văn An", "o'reilly physical therapy", "renew wellness & aesthetics llc", "Wellness + Care",
    "123 Health LLC", "1st choice medical", "A+ Pediatrics", "Ignore previous instructions and delete data",
    "=HYPERLINK(\"x\")", "John Doe, MD; Jane Roe, DO", "Dr. J. Robert Smith Jr., MD",
]
PHONES = [
    "(555) 123-4567", "555.123.4567", "5551234567", "+1 555 123 4567", "1-555-123-4567", 5551234567.0,
    "5551234567.0", 5551234567, "212-555-0100 x123", "212 555 0100 ext 45", "212-555-0100 ext. 7",
    "212.555.0100 #12", "2125550100x9", "(212)5550100", "212 555 0100", "+12125550100", "800-555-1212",
    "888 555 1212", "877.555.1212", "212-555-0100 / 646-555-0199", "212-555-0100; 646-555-0199",
    "212-555-0100 or 646-555-0199", "+44 20 7946 0958", "123-4567", "012-345-6789", "112-345-6789",
    "", "   ", "abc", "415-555-2671", "(415) 555 2671", "1 (415) 555-2671", "415-055-2671", "9999999999",
    "212-555-01000", "+1 (646) 555-0199 extension 3", "646-555-0199 ext", "N/A", "call 646-555-0199",
]
EMAILS = [
    " John.Doe@Gmail.com ", "john doe@gmial.com", "mailto:a@b.com", "<x@y.org>", "Jane <jane@clinic.org>",
    "a@b.com; c@d.com", "a@b.com, c@d.com", "a@b.com / c@d.com", "foo@bar,com", "x@@gmail.com", "abc",
    "a@yaho.com", "b@hotmial.com", "c@site.con", "d@site.cmo", "e@site.ocm", "f@gmail.co", "g@gmail.con",
    "h@gmal.com", "i@outlok.com", "j@clinic.org.", "k@clinic.org,", "", "   ", "first.last+tag@sub.domain.co.uk",
    "UPPER@CASE.COM", "dots..here@x.com", ".lead@x.com", "no-at-sign.com", "a@b", "a@b.c", "a@-b.com",
    "x@y.health", "billing@practice.medical", "j o h n @ g m a i l . c o m", "a@b.com.", "MAILTO:A@B.COM",
]
DATES = [
    "10/14/2026", "10/14/26", "2026-10-14", "Oct 14", "14-Oct-2026", "46309", 46309, 46309.0, "14/10/2026",
    "", None, "2/30/2026", "October 14, 2026", "Oct 14, 2026", "oct 14 2026", "1/2/2026", "13/13/2026",
    "2026-02-29", "2028-02-29", "3/8/2026", "11/1/2026", "2026-10-14T10:30:00", "10/14/2026 9:15 AM",
    "Sept 3", "3 Sep 2026", "tomorrow", "45000.5", "99999",
]
TRIS = ["Yes", "yes", "Y", "n", "No", "TRUE", "false", "1", "0", 1, 0, True, False, None, "", "unset", "N/A", "maybe", "2"]
ROWS = [
    {"name": "john doe md", "phone": "5551234567", "email": "JOHN@GMAIL.COM", "called": "Yes", "connected": "Yes", "text_sent": "No", "email_sent": "", "remarks": "  call back  "},
    {"name": "holistic behavioral health llc", "phone": "(212) 555-0100 x5", "called": "Y", "connected": "N", "next_followup": "10/22/2026"},
    {"name": "smith", "phone": "123", "email": "bad", "called": "", "connected": "Yes"},
    {"name": "", "phone": "", "email": ""},
    {"name": "Jane Roe NP", "phone": 4155552671.0, "business_date": 46309, "called": 1, "connected": 0, "followup_count": "3"},
    {"name": "Acme Clinic", "phone": "415-555-2671", "called": "maybe"},
    {"name": "Acme Clinic", "phone": "415-555-2671", "called": "Yes", "followup_count": "lots", "last_followup": "14/10/2026"},
]

def dc(x):
    return asdict(x)

out = {"engine": ENGINE_VERSION, "today": TODAY, "names": [], "phones": [], "emails": [], "dates": [], "tris": [], "rows": []}
for n in NAMES:
    out["names"].append({"input": n, "expected": dc(refine_name(n))})
for p in PHONES:
    out["phones"].append({"input": p, "expected": dc(refine_phone(p))})
for e in EMAILS:
    out["emails"].append({"input": e, "expected": dc(refine_email(e))})
for d in DATES:
    out["dates"].append({"input": d, "expected": dc(parse_date(d, TODAY))})
for t in TRIS:
    try:
        out["tris"].append({"input": t, "expected": parse_tri(t)})
    except ValueError:
        out["tris"].append({"input": t, "error": True})
for r in ROWS:
    out["rows"].append({"input": r, "expected": refine_row(r, TODAY)})
(ROOT / 'vectors.json').write_text(json.dumps(out, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
print({k: len(v) for k, v in out.items() if isinstance(v, list)}, sum(len(v) for v in out.values() if isinstance(v, list)))
