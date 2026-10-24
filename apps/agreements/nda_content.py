"""Mutual Nondisclosure Agreement for the contracting companies.

New drafting (not an approved client source): LEGAL_REVIEW.md asks counsel to approve it before first use.
Governing state, dates, parties and the NDA options come from the same validated form as the other agreements.
"""
from contract_content import block, _clean, _date
from entities import get_entity
from revised_content import jurisdiction

WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten'}


def years(n):
    n = int(n)
    return f"{WORDS[n]} ({n}) year{'s' if n != 1 else ''}"


def build_nda(data, contract_id):
    entity = get_entity(data.get('entity'))
    company, short = entity['name'], entity['short']
    client = _clean(data['client_name'])
    effective = _date(data['effective_date'])
    state = _clean(data.get('governing_state')) or entity['governing_state']
    purpose = _clean(data.get('nda_purpose')).rstrip('.')
    term, survival = years(data.get('nda_term_years', 2)), years(data.get('nda_survival_years', 3))
    provider_address = _clean(data.get('provider_address')) or entity['address']
    client_notice = _clean(data.get('client_email')) or 'the authorized signatory at the address stated above'
    company_notice = _clean(data.get('provider_email')) or f"the {data.get('_signatory_title') or entity['title']} at the address stated above"
    sections = [
        ('1. Purpose', [
            f'The Parties wish to exchange information solely for the purpose of {purpose} (the "Purpose"). This Agreement governs Confidential Information either Party discloses for the Purpose. It does not obligate either Party to disclose any particular information or to enter into any further agreement or transaction.']),
        ('2. Confidential Information', [
            '"Confidential Information" means non-public information disclosed by or on behalf of a Party ("Discloser") to the other Party ("Recipient"), in any form or medium, that is marked or identified as confidential or that a reasonable person would understand to be confidential from its nature and the circumstances of disclosure. It includes business plans, pricing and proposals, financial information, client and payer information, workflows, policies, personnel information, software, system access details and credentials, security information, and the existence and terms of discussions between the Parties.',
            'Confidential Information does not include information that the Recipient can show by written records (a) is or becomes generally available to the public without breach of this Agreement; (b) was lawfully known to the Recipient without a duty of confidentiality before receipt from the Discloser; (c) is lawfully received from a third party without a duty of confidentiality; or (d) is independently developed by the Recipient without use of or reference to the Discloser\'s Confidential Information.']),
        ('3. Protected Health Information', [
            'This Agreement does not authorize the disclosure of protected health information as defined by HIPAA ("PHI"). PHI may be disclosed only under a business associate agreement that complies with HIPAA and applicable law. If PHI is disclosed, that business associate agreement and applicable law govern the PHI and control over this Agreement to the extent of any conflict.']),
        ('4. Obligations of the Recipient', [
            'The Recipient shall (a) use Confidential Information only for the Purpose; (b) protect it with at least the degree of care it uses for its own information of similar importance, and no less than reasonable care; (c) not disclose it except to its employees, contractors and professional advisers who need to know it for the Purpose and are bound by written or professional confidentiality obligations at least as protective as this Agreement ("Representatives"); and (d) not copy, reverse engineer, decompile or analyze it except as reasonably required for the Purpose.',
            'The Recipient is responsible for any act or omission of its Representatives that would breach this Agreement if done by the Recipient. The Recipient shall notify the Discloser in writing promptly, and in any event within five business days, after discovering any unauthorized access to, use of or disclosure of Confidential Information, and shall cooperate reasonably to contain and remedy it.']),
        ('5. Legally Required Disclosure', [
            'If the Recipient or its Representative is required by law, regulation, subpoena or court order to disclose Confidential Information, it shall, to the extent legally permitted, give the Discloser prompt written notice so that the Discloser may seek a protective order or other remedy, cooperate reasonably with that effort, and disclose only the portion that it is legally required to disclose. Nothing in this Agreement prohibits a report to a government authority that is protected by law.']),
        ('6. Return or Destruction', [
            'Within thirty days after the Discloser\'s written request, or after the Purpose ends, the Recipient shall return or securely destroy the Discloser\'s Confidential Information and, on request, confirm this in writing. The Recipient may retain copies required by law or professional record-keeping rules and copies in automatic backup systems that are not readily accessible; retained copies remain subject to this Agreement for as long as they are held.']),
        ('7. No License or Warranty', [
            'All Confidential Information remains the property of the Discloser. No license or other right is granted except the limited right to use Confidential Information for the Purpose. Confidential Information is provided "as is" without warranty of accuracy or completeness, except as the Parties expressly agree in a separate signed agreement.']),
        ('8. Term and Survival', [
            f'This Agreement begins on {effective} and applies to Confidential Information disclosed during {term} from that date, unless either Party ends it earlier by thirty days\' written notice. The Recipient\'s obligations continue for {survival} after this Agreement expires or ends, and for trade secrets for as long as they remain trade secrets under applicable law.']),
        ('9. Remedies', [
            'Unauthorized use or disclosure of Confidential Information may cause irreparable harm for which damages are an inadequate remedy. The Discloser may seek injunctive or other equitable relief, without posting a bond to the extent permitted by law, in addition to any other remedy available at law or in equity.']),
        ('10. General Terms', [
            f'The laws of {jurisdiction(state)}, excluding conflict of law rules, govern this Agreement, subject to controlling federal law. Any court proceeding shall be brought in a court of competent jurisdiction in that state unless applicable law requires otherwise.',
            f'Notices shall be in writing and sent to the addresses stated above. {client} notice contact: {client_notice}. {company} notice contact: {company_notice}. Either Party may update its notice contact by written notice.',
            'This Agreement is the entire agreement of the Parties on its subject. Where the Parties have signed a services agreement or business associate agreement, any confidentiality or information-protection term of that agreement that is more protective continues to apply. Amendments and waivers must be in writing and signed by authorized representatives. Neither Party may assign this Agreement without the other Party\'s written consent, except to a successor to substantially all of its business that agrees in writing to be bound. If a provision is unenforceable, the remaining provisions continue to the extent lawful. Nothing in this Agreement creates a partnership, joint venture or agency.',
            'This Agreement may be executed in counterparts and by electronic signature, each of which is deemed an original and all of which together form one instrument. Electronic signatures and electronically signed records have the same effect as handwritten signatures and paper records to the extent permitted by law.']),
    ]
    b = [block('title', 'MUTUAL NONDISCLOSURE AGREEMENT'),
         block('p', f'Agreement reference: {contract_id} | Effective Date: {effective}'),
         block('p', f'This Mutual Nondisclosure Agreement ("Agreement") is effective {effective} between {company}, with its address at {provider_address} ("{short}"), and {client}, with its address at {_clean(data["address"])} ("Client"). Each is a "Party" and together they are the "Parties." Either Party may disclose or receive Confidential Information under this Agreement. The Parties agree as follows.')]
    for heading, paragraphs in sections:
        b.append(block('h1', heading))
        b += [block('p', text) for text in paragraphs]
    b.append(block('signature', f'The undersigned agree to this Mutual Nondisclosure Agreement, reference {contract_id}, effective {effective}, between {company} and {client}.'))
    return b
