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
        scopes.append(scope)
        out.append(B('h2',f'1.{i} {service_name(s)}'))
        if s['id'] in source_scopes:
            for source_block in source_scopes[s['id']]['scope']:
                b=deepcopy(source_block)
                b['text']=b['text'].replace('PRACTICE NAME',data['client_name'])
                out.append(b)
        else:
            out.append(B('p',scope[0]))
        if s.get('details'):
            out.append(B('h3',f'1.{i}.1 Agreed Service Parameters'))
            out.extend(original._paragraphs(s['details']))
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
            for text in common: out.append(B('p',text))
        for scope in scopes:
            if key(scope[role]) in [key(x) for x in common]: scope[role]=''
    for i,(s,scope) in enumerate(zip(chosen,scopes),2):
        if scope[1] or scope[2]:
            out.append(B('h2',f'2.{i} {service_name(s)}'))
            for role,label in [(1,'Scribe Align Responsibilities'),(2,'Client Responsibilities')]:
                if scope[role]: out += [B('h3',label),B('p',scope[role])]
    out += [B('h1','3. Fees'),B('h2','3.1 Initial Setup'),B('p','There is no charge for the initial set up of the services.')]
    for i,s in enumerate(chosen,2):
        out.append(B('h2',f'3.{i} {service_name(s)}'))
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
                    out += [B('h2',f'9.{i} {service_name(s)}: Limitations and Exclusions'),B('p',scope[3])]
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
