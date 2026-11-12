"""Approved BAA source and section-based service composition.

Service source replacements must be approved before incorporation. Until then,
the existing service text is retained, with structure and pricing separated.
"""
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import re
import contract_content as original
from entities import get_entity
from format_guard import structure

CATALOG = deepcopy(original.CATALOG)
for spec in CATALOG:
    if spec['id'] == 'credentialing':
        spec['models'].insert(0, {'id':'rate_card','label':'Government / commercial payer rates'})

GOVERNING_LAW = 'the laws of the State of Georgia'
def jurisdiction(state):
    return 'the District of Columbia' if state == 'District of Columbia' else 'the State of '+state

def build_baa(data, contract_id):
    source = json.loads((Path(__file__).parent/'templates'/'baa.json').read_text(encoding='utf-8'))
    effective = date.fromisoformat(data['effective_date'])
    # The approved source names Georgia; the governing state chosen for the package (the contracting company's state by
    # default) applies to the BAA as well. GOVERNING_LAW must match the source exactly once (tested).
    state=data.get('governing_state') or get_entity(data.get('entity'))['governing_state']
    replacements = {'PRACTICE NAME':data['client_name'], source['sample_date']:f'{effective.month}/{effective.day}/{effective.year}','SCRIBE ALIGN LLC':get_entity(data.get('entity'))['name'],
                    GOVERNING_LAW:'the laws of '+jurisdiction(state)}
    def replace(text):
        for old,new in replacements.items():
            text=text.replace(old,new)
        return text
    blocks=deepcopy(source['blocks'])
    for b in blocks:
        b['text']=replace(b['text'])
        # Preserve inline bold labels even where a placeholder spans Word runs.
        if b.get('runs'):
            runs=b['runs']
            for r in runs:
                r['text']=replace(r['text'])
            if ''.join(r['text'] for r in runs).strip()!=b['text']:
                b['runs']=[{'text':b['text'],'bold':False}]
    return blocks

def service_name(s):
    if s.get('id')=='website_development':
        return 'WordPress Website Development' if s.get('website_kind','wordpress')=='wordpress' else 'Custom Website Development'
    return s['name']

OPTION_LABEL='Fee option selected by Client for'
LETTERS='ABCDE'


def fee_description(s):
    """One pricing option in words (the standard wording, or the wording of a model added under Admin)."""
    if s.get('model_def'):
        import catalog_admin
        return catalog_admin.fee_text(s['model_def'], s.get('values') or {})
    if s['model']=='rate_card':
        return f"{original._money(s['government_rate'])} per provider per application for government payers; {original._money(s['commercial_rate'])} for commercial payers, when asked by Client."
    if s['model']=='percentage':
        return original._plain_number(s['rate'])+'% of total monthly net collections, as defined in Section 5.1; invoiced monthly in arrears.'
    return original._fee_description(s)


def fee_rows(s):
    options=s.get('options') or []
    if len(options)>1:
        return [[f'Option {LETTERS[i]}', fee_description(dict(original._service_record(s),**o))] for i,o in enumerate(options)]
    return [[service_name(s), fee_description(s)]]


def build_services(data, contract_id):
    # Legacy blocks supply the unchanged common terms, selected by applicability.
    prior=original.build_services(data,contract_id)
    groups={}; intro=[]; current=None; execution=None
    for b in prior:
        if b['type']=='signature': execution=b; continue
        if b['type']=='h1':
            current=int(b['text'].split('.')[0]); groups[current]=[]; continue
        (intro if current is None else groups[current]).append(deepcopy(b))
    B=original.block
    chosen=[original._service_record(s) for s in data.get('services',[])+data.get('custom_services',[])]
    out=intro+[B('h1','1. Services')]
    for b in groups[1]:
        b['text']=b['text'].replace('services expressly selected in Section 3','services expressly selected in Section 1')
        out.append(b)
    scopes=[]
    source_scopes=json.loads((Path(__file__).parent/'templates/service-scopes.json').read_text(encoding='utf-8'))
    for i,s in enumerate(chosen,1):
        scope=list(original.SCOPES.get(s['id'],original.SCOPES['other']))
        for key,pos in [('scope',0),('responsibilities',2),('exclusions',3)]:
            if s.get(key): scope[pos]=s[key]
        if s['id']=='patient_help_desk':   # its exclusions refer to its own scope section
            scope[3]=scope[3].replace('described in Section 1.1,',f'described in Section 1.{i},')
        scopes.append(scope)
        out.append(B('h2',f'1.{i} {service_name(s)}'))
        if s.get('responsibilities_admin'):   # wording set under Admin > Services & pricing
            scope[1], scope[2] = s['responsibilities_admin'], ''
        if s['id'] in source_scopes and not s.get('scope_custom'):
            for source_block in source_scopes[s['id']]['scope']:
                b=deepcopy(source_block)
                b['text']=b['text'].replace('PRACTICE NAME',data['client_name'])
                out.append(b)
        else:
            # Typed, pasted and company-specific wording prints in the agreement's own formatting (format_guard);
            # a standard single paragraph comes out unchanged.
            out.extend(structure(scope[0]))
        if s.get('details'):
            out.append(B('h3',f'1.{i}.1 Agreed Service Parameters'))
            out.extend(structure(s['details']))
    out += [B('h1','2. Responsibilities'),B('h2','2.1 General Responsibilities')]+groups[5]
    # Only identical paragraphs in the same role are common. Similar wording is
    # never treated as interchangeable; that would silently change obligations.
    key=lambda text:re.sub(r'\s+',' ',text).strip().casefold()
    for role,label in [(1,'Scribe Align Responsibilities'),(2,'Client Responsibilities')]:
        common=[]
        for scope in scopes:
            if sum(key(x[role])==key(scope[role]) for x in scopes)>1 and key(scope[role]) not in [key(x) for x in common]:
                common.append(scope[role])
        if common:
            out.append(B('h3',label+' for the Applicable Services'))
            for text in common: out.extend(structure(text))
        for scope in scopes:
            if key(scope[role]) in [key(x) for x in common]: scope[role]=''
    for i,(s,scope) in enumerate(zip(chosen,scopes),2):
        if scope[1] or scope[2]:
            out.append(B('h2',f'2.{i} {service_name(s)}'))
            for role,label in [(1,'Scribe Align Responsibilities'),(2,'Client Responsibilities')]:
                if scope[role]: out += [B('h3',label)]+structure(scope[role])
    out += [B('h1','3. Fees'),B('h2','3.1 Initial Setup'),B('p','There is no charge for the initial set up of the services.')]
    for i,s in enumerate(chosen,2):
        out.append(B('h2',f'3.{i} {service_name(s)}'))
        if len(s.get('options') or [])>1:
            out.append(B('p','The Client may choose one of the following fee options for this service. The option the Client selects when signing this Agreement applies for its term.'))
            out.append({'type':'table','headers':['Option','Fee (USD, unless stated as a percentage)'],'rows':fee_rows(s)})
            out.append(B('p',f'{OPTION_LABEL} {service_name(s)}: ______________________'))
            continue
        if s.get('model_def'):
            out.append({'type':'table','headers':['Service','Fee (USD, unless stated as a percentage)'],'rows':fee_rows(s)})
            continue
        if s['model']=='rate_card':
            out.append(B('p','The Credentialing services will be provided when asked by Client on the following rates.'))
            rows=[['Credentialing/provider/application (for Govt. Payers)',original._money(s['government_rate'])],['Credentialing/provider/application (for Commercial Payers)',original._money(s['commercial_rate'])]]
        elif s['model']=='percentage':
            out.append(B('p','The fee for services will be based on a percentage of total monthly net collections, as defined in Section 5.1.'))
            rows=[[service_name(s),original._plain_number(s['rate'])+'%']]
        else:
            rows=[[service_name(s),original._fee_description(s)]]
        out.append({'type':'table','headers':['Service','Fee (USD, unless stated as a percentage)'],'rows':rows})
    fee_intro=next(b for b in groups[3] if b['type']=='p' and b['text'].startswith('All fees'))
    out.append(fee_intro)
    out += [B('h1','4. Term and Service Administration')]+groups[2]
    out += [B('h1','5. Invoicing and Payment')]
    for b in groups[4]:
        if b['type']=='h2': b['text']=b['text'].replace('4.1','5.1',1)
        out.append(b)
    for n,title in [(6,'Confidentiality and Information Security'),(7,'Records and Work Product'),(8,'Termination, Suspension and Transition'),(9,'Responsibility and Remedies'),(10,'Notices and General Terms')]:
        out.append(B('h1',f'{n}. {title}'))
        for b in groups[n]:
            b['text']=b['text'].replace('Termination for convenience is governed by Section 2.','Termination for convenience is governed by Section 4.')
            out.append(b)
        if n==9:
            seen=set()
            for i,(s,scope) in enumerate(zip(chosen,scopes),1):
                if scope[3] and key(scope[3]) not in seen:
                    out += [B('h2',f'9.{i} {service_name(s)}: Limitations and Exclusions')]+structure(scope[3])
                    seen.add(key(scope[3]))
    if 11 in groups: out += [B('h1','11. Special Terms')]+groups[11]
    execution['text']=execution['text'].replace('services and fees stated in Section 3','services stated in Section 1 and fees stated in Section 3')
    out.append(execution)
    entity=get_entity(data.get('entity'))
    if entity['name']!='SCRIBE ALIGN LLC':
        def substitute(value):
            if isinstance(value,str): return value.replace('SCRIBE ALIGN LLC',entity['name']).replace('Scribe Align',entity['short'])
            if isinstance(value,list): return [substitute(x) for x in value]
            if isinstance(value,dict): return {k:substitute(v) for k,v in value.items()}
            return value
        out=substitute(out)
    return out
