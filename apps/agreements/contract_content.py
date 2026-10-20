"""Deterministic contract language and service catalog for Scribe Align.

This module produces document blocks only. Business inputs are validated by the
application. Counsel review notes are in LEGAL_REVIEW.md, outside the contracts.
"""
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TEMPLATE_VERSION = "2026.10.13.1"
ENTITY = "SCRIBE ALIGN LLC"
DEFAULT_ADDRESS = "9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134"

MODELS = {
    "percentage": "Percentage of net collections",
    "fte": "FTE per month",
    "pte": "PTE per month",
    "unit": "Per completed unit",
    "fixed": "Fixed monthly fee",
    "consultancy": "Monthly consultancy fee",
    "one_time": "One project invoice",
    "two_installments": "Two project installments",
}

def _item(identifier, name, category, models, description):
    return {"id": identifier, "name": name, "category": category,
            "models": [{"id": m, "label": MODELS[m]} for m in models],
            "description": description}

CATALOG = [
    _item("medical_billing", "Medical Billing, Including Coding", "recurring", ["percentage", "fte"], "Claim preparation, coding review, submission, payment posting and ordinary payer follow-up."),
    _item("wc_collection", "WC Collection and Litigation Support", "recurring", ["percentage"], "Administrative workers' compensation collection and support for Client's retained counsel."),
    _item("personal_injury_collection", "Personal Injury Collection Services", "recurring", ["fte", "percentage"], "Assigned personal injury medical receivable follow-up, attorney and insurer coordination, reconciliation and collection reporting."),
    _item("medical_coding", "Medical Coding Consultancy", "recurring", ["consultancy"], "Documentation-based code review, coding guidance and education."),
    _item("prior_auth", "Prior Authorizations and Verification of Benefits", "recurring", ["unit", "fte", "pte"], "Benefit verification, authorization submissions and status tracking."),
    _item("virtual_assistant", "Virtual Assistant Services", "recurring", ["fte", "pte"], "Nonclinical scheduling, communication and administrative assistance."),
    _item("medical_scribe", "Virtual Live or Offline Medical Scribe", "recurring", ["fte", "pte"], "Encounter documentation support subject to clinician review and authentication."),
    _item("ar_denials", "AR and Denial Management", "recurring", ["fte", "pte"], "Assigned receivable follow-up, denial analysis and administrative appeals."),
    _item("rcm_consultancy", "RCM Consultancy", "recurring", ["fte", "pte"], "Revenue cycle assessment, workflow recommendations and agreed implementation assistance."),
    _item("digital_marketing", "Digital Marketing", "recurring", ["fixed"], "Approved campaign planning, content and performance reporting."),
    _item("telemarketing", "Telemarketing", "recurring", ["fixed"], "Approved outreach campaigns with consent and suppression controls."),
    _item("credentialing", "Credentialing and Enrollment", "on_demand", ["one_time", "two_installments"], "Defined provider and payer applications, follow-up and deficiency coordination."),
    _item("state_licensing", "State Licensing Services", "on_demand", ["one_time", "two_installments"], "Administrative preparation and tracking of identified licensing applications."),
    _item("compliance_training", "Compliance Training", "on_demand", ["one_time", "two_installments"], "Defined training materials, instruction and attendance documentation."),
    _item("website_development", "Website Development", "on_demand", ["one_time", "two_installments"], "An agreed website specification, development, review and handover."),
    _item("consultancy", "Consultancy Services", "on_demand", ["one_time", "two_installments"], "A defined advisory assessment and written recommendations."),
    _item("mips_consultancy", "MIPS Consultancy", "on_demand", ["one_time", "two_installments"], "Performance year planning, data checks and authorized submission support."),
    _item("coding_consultancy", "Coding Consultancy", "on_demand", ["one_time", "two_installments"], "A defined coding review sample, findings and education."),
    _item("other", "Other Services", "on_demand", ["one_time", "two_installments"], "A separately described administrative or advisory project."),
]
CATALOG_BY_ID = {s["id"]: s for s in CATALOG}

# Each selected service contributes only its own scope, responsibilities and limits.
SCOPES = {
    "medical_billing": (
        "Scribe Align shall review assigned encounter documentation for claim readiness, assign or review supported diagnosis and procedure codes, prepare and submit authorized claims, post available remittance information, reconcile payment records and perform ordinary rejection and denial follow-up. Work is limited to the providers, payers, dates of service and systems identified in the service parameters.",
        "Scribe Align shall apply current coding conventions and payer instructions relevant to the assigned work, flag missing documentation and suspected duplicate or unsupported claims, maintain submission and follow-up records, and provide a monthly activity and collections report. Ambiguous clinical documentation shall be referred to Client for clarification before affected claims are submitted.",
        "Client shall provide complete, accurate and authenticated records, enrollment details, payer agreements, fee schedules and remittances promptly; approve material billing policies and corrections; answer documentation queries; and retain responsibility for medical necessity, services furnished and clinician-authenticated documentation. Client shall authorize and make required refunds or overpayment reports without delay.",
        "No code shall be selected solely to increase payment or supplied without documentation support. Scribe Align shall not fabricate records, unbundle services, waive patient obligations without lawful authorization, settle claims or change clinical records independently. Recovery litigation, special audits, credentialing and collection of unassigned historical receivables are excluded unless expressly included in this Agreement."
    ),
    "wc_collection": (
        "Scribe Align shall maintain an inventory of assigned workers' compensation receivables, reconcile available payment records, prepare permitted administrative reimbursement requests, follow up with authorized payer contacts and assemble records for Client's licensed attorney. Any lien filing or administrative filing requires prior written approval and may be performed only when lawful without a professional license.",
        "Scribe Align shall document communications, identify disputed balances and apparent deadlines, report collection activity monthly, and refer legal questions and contested proceedings to Client's counsel. It shall promptly route legal notices and settlement proposals to Client without accepting them.",
        "Client shall supply accurate treatment, billing, employment and claim records; identify the applicable jurisdictions and deadlines; retain counsel for legal work; approve authorized communications; and decide settlements, waivers and litigation strategy. Client and its counsel remain responsible for legal filings and preservation of rights.",
        "Litigation support is administrative only. Scribe Align is not retained as a law firm, legal representative or insurer, shall not appear as counsel, give legal advice or negotiate legal settlements, and shall not receive a share of attorneys' fees. Court, counsel and permitted filing charges require separate Client approval. No recovery or adjudication result is guaranteed."
    ),
    "personal_injury_collection": (
        "Scribe Align shall provide administrative collection services for Client's assigned medical receivables arising from personal injury matters. Services include maintaining an account inventory, reviewing supplied billing and payment records, transmitting authorized bills and supporting records, following up with designated insurers and attorneys, tracking reported case and payment status, reconciling receipts and reporting outstanding balances. The service parameters shall identify the assigned providers, accounts, jurisdictions, authorized contacts and, for FTE services, staffing allocation and coverage hours.",
        "Scribe Align shall maintain account-level follow-up records, verify balances against available records, document authorized communications, provide monthly activity and collections reports, and promptly refer disputes, legal notices, apparent deadlines and proposed reductions or settlements to Client. Disclosures of protected health information shall be limited to the minimum necessary for the permitted purpose and made through approved channels under the applicable Business Associate Agreement. Scribe Align shall comply with collection, communication and licensing requirements applicable to its activities and shall not undertake an activity requiring a license it does not hold.",
        "Client shall identify the accounts assigned for collection; provide accurate treatment, billing, payment, insurance and attorney information and applicable authorizations; identify existing liens, letters of protection, disputes and known deadlines; and promptly report receipts, refunds and adjustments. Client shall approve reductions, write-offs and settlements, retain qualified counsel for legal determinations and proceedings, and remain responsible for lien validity, filings, enforcement and preservation of legal rights. Payments shall be directed to Client-controlled accounts. Where another selected service also handles an assigned account, Client and Scribe Align shall document the allocation of work and receipts before invoicing; the same receipt shall not be charged a percentage fee more than once under this Agreement.",
        "Services do not include legal advice, representation, litigation, lien creation or enforcement, negotiation of legal settlements, receipt or custody of settlement funds, or credit reporting. Patient-directed debt collection is excluded unless separately agreed in writing after applicable authorization and licensing requirements have been established. Scribe Align shall not bind Client to a compromise or receive a share of attorneys' fees. If percentage pricing is selected, fees apply only to Eligible Net Collections from the assigned medical receivables as defined in this Agreement, not to total personal injury settlement proceeds or attorneys' fees. No recovery amount, settlement date or payment outcome is guaranteed."
    ),
    "medical_coding": (
        "Scribe Align shall provide recurring advisory review of the assigned documentation and coding questions, prepare supported code recommendations, identify documentation gaps and deliver agreed coding education or reports. The service parameters shall identify specialties, review volumes, applicable code sets and response expectations.",
        "Scribe Align shall use the coding rules applicable to the relevant date of service, distinguish documented facts from queries requiring clinician response, and explain material findings with an auditable review record. It shall escalate suspected unsupported coding or payment concerns to Client.",
        "Client shall provide complete records and access to relevant payer policies, designate a clinician to resolve documentation questions, review recommendations and determine their adoption. Client retains responsibility for authenticated clinical content, final claim certification and any required corrections or repayments.",
        "This advisory service does not include routine claim submission, clinical diagnosis, legal opinions, payer representation or a certification of complete coding compliance. A sample review does not establish the accuracy of records outside that sample."
    ),
    "prior_auth": (
        "Scribe Align shall verify available eligibility and benefit information, identify stated authorization requirements, prepare requests from Client-approved clinical material, submit them through authorized channels and record payer responses and follow-up status for the assigned cases.",
        "Scribe Align shall report source, date and reference information for verifications when available; flag missing records, denials and approaching deadlines; and route clinical questions or requests for peer review to Client. A completed unit means one case for the named patient, planned service and payer, including ordinary status follow-up, unless a different unit is expressly defined below.",
        "Client shall provide accurate patient and insurance details, planned service dates, orders, clinical justification and prompt clinician responses. Client shall determine whether and when treatment proceeds, verify critical coverage information before service and conduct any clinical peer-to-peer review.",
        "Benefit verification and authorization are payer information, not guarantees of eligibility, coverage, medical necessity or payment. Clinical decisions, emergency triage, appeals requiring a clinician or lawyer, and authorization of services on Client's behalf beyond written instructions are excluded."
    ),
    "virtual_assistant": (
        "Scribe Align shall furnish remote administrative personnel for the agreed schedule and assigned nonclinical tasks, which may include scheduling, approved call handling, inbox routing, data entry and document coordination. Specific duties and coverage hours shall be those stated in the service parameters.",
        "Scribe Align shall train assigned personnel on the approved workflow, protect access credentials, keep reasonable task records and promptly escalate clinical, urgent or unresolved matters to Client's designated contact. It shall arrange a qualified replacement when reasonably necessary and notify Client of material coverage changes.",
        "Client shall supply operating procedures, escalation contacts, approved scripts, access permissions and scheduling rules, and maintain a responsive clinical escalation channel. Client shall oversee patient care and review decisions reserved to licensed personnel.",
        "Personnel shall not diagnose, prescribe, independently triage patients, give medical advice, sign clinician records or impersonate licensed staff. Emergency services and continuous coverage are excluded unless an express written staffing schedule provides otherwise."
    ),
    "medical_scribe": (
        "Scribe Align shall prepare draft encounter documentation from Client-authorized live sessions, dictation or recordings, using Client's approved electronic health record workflow. The service parameters shall identify live or offline service, specialties, coverage hours and expected turnaround.",
        "Scribe Align shall accurately reflect source material, flag ambiguities, keep drafts identifiable as requiring clinician review and restrict recordings and transcripts to approved systems and retention periods. Personnel shall not finalize or authenticate a clinician's note using the clinician's credentials.",
        "Client shall obtain all required patient notices and permissions, provide secure source material and timely clarification, and cause the treating clinician to review, correct and authenticate every final note. Client shall control clinical content, orders, decision-making and any required record completion deadline.",
        "Scribing is documentation assistance only and does not transfer clinical responsibility. Scribe Align shall not make independent diagnoses, orders or treatment decisions. Audio or video recording requires lawful authorization and documented retention instructions; use of automated transcription or generative systems requires prior written approval and appropriate data protections."
    ),
    "ar_denials": (
        "Scribe Align shall analyze assigned aging accounts, classify denials, reconcile available remittances, conduct payer status inquiries, prepare supported administrative corrections or appeals and maintain a work log and monthly exception report.",
        "Scribe Align shall prioritize work using the agreed aging and deadline criteria, preserve available submission evidence, flag suspected overpayments and refer clinical or legal issues to Client. Corrections shall be based on verified records and approved billing policy.",
        "Client shall identify the assigned account population, provide records and original submission evidence, respond to documentation requests, approve material adjustments and settlement proposals and ensure timely clinician input for clinical appeals.",
        "No recovery rate or payer outcome is guaranteed. Legal collection, lawsuits, representation in hearings, patient debt collection requiring a license, unauthorized write-offs and accounts outside the assigned population are excluded."
    ),
    "rcm_consultancy": (
        "Scribe Align shall assess the agreed revenue cycle processes, review available operating data, identify administrative improvement opportunities and provide an action plan with agreed implementation assistance and progress reporting.",
        "Scribe Align shall identify the data reviewed and material assumptions, distinguish observations from estimates and discuss recommendations with Client's designated management team. Work shall be performed within the personnel allocation and schedule stated below.",
        "Client shall provide accurate operational information, responsible staff and access, approve changes before implementation and retain control over payer contracts, staffing, clinical matters and business decisions.",
        "Recommendations do not assure increased revenue, a specific collection level or regulatory approval. Independent financial audit, legal or tax advice, routine billing operations and clinical management are excluded unless separately selected and expressly described."
    ),
    "digital_marketing": (
        "Scribe Align shall develop and administer the approved digital marketing plan, which shall identify channels, content deliverables, posting frequency and reporting measures. Campaign publication and material expenditure require Client's prior approval.",
        "Scribe Align shall use approved claims and creative material, maintain agreed account access, report campaign activity and expenditure and respect recorded opt-outs. It shall obtain Client approval before publishing patient images, testimonials or clinical claims and shall not create misleading endorsements or unsupported outcome claims.",
        "Client shall substantiate advertising claims, approve all clinical statements and uses of its marks, supply rights-cleared materials, fund approved third-party advertising costs and obtain legally required permissions. Client shall approve the campaign audience and privacy notices before launch.",
        "PHI shall not be disclosed to advertising platforms, tracking tools or audience services, or used for marketing, without a separately documented lawful basis and any required individual HIPAA authorization. A BAA or Client's approval alone does not supply that authorization. Search rankings, leads and conversions are not guaranteed; ad spend is excluded from the service fee unless expressly stated."
    ),
    "telemarketing": (
        "Scribe Align shall conduct only the outreach campaign described in the service parameters, using Client-approved scripts, seller identification, contact channels, operating hours and calling technology. The campaign must be approved in writing before the first contact.",
        "Scribe Align shall apply applicable consent, do-not-call, opt-out, caller identification, calling-hour and recordkeeping requirements; screen approved lists and honor revocations; maintain campaign and consent records required for its activities; and promptly report complaints. Calls shall not be recorded without any required consent.",
        "Client shall identify the seller and campaign purpose, substantiate statements, provide lawful contact sources and verifiable consent records, approve scripts and make required disclosures. Each party remains responsible for legal duties imposed directly on it and shall cooperate in suppression and complaint handling.",
        "Automated dialing, prerecorded or artificial voice calls, text campaigns and use of PHI require separate written campaign approval and legally sufficient permissions where required. Purchased lists do not establish consent. Misleading statements, unlawful solicitation, legal or medical advice and unauthorized disclosure of patient status are prohibited. Results are not guaranteed."
    ),
    "credentialing": (
        "Scribe Align shall prepare and track the provider credentialing or enrollment applications specifically identified below, compile Client-supplied materials, coordinate ordinary requests for missing information and report submission and response status. The service parameters shall identify each provider, payer, location and application type.",
        "Scribe Align shall check administrative completeness against available application instructions, obtain Client approval before submission, preserve submitted copies and provide reasonable ordinary follow-up through the agreed project milestone. It shall promptly route adverse findings and requests for an authorized signature to Client.",
        "Client shall provide accurate licenses, insurance, education, ownership and other required disclosures; timely review all statements; execute required attestations through an authorized individual; and disclose changes and adverse actions. Client remains responsible for continuing eligibility, revalidation and payer participation decisions.",
        "Payers and licensing or accreditation bodies alone decide participation, privileges, effective dates and processing times. Approval, retroactive reimbursement and network admission are not guaranteed. Application fees, legal appeals, new applications caused by material Client changes and ongoing maintenance are excluded unless expressly listed."
    ),
    "state_licensing": (
        "Scribe Align shall provide administrative support for the identified license type, applicant and jurisdiction by collecting materials, completing draft applications, tracking submissions and coordinating ordinary deficiency responses through the agreed milestone.",
        "Scribe Align shall use current published application instructions, identify missing documents, preserve copies and refer legal eligibility or disciplinary questions to Client and its counsel. It shall submit an application only after the applicant or authorized representative approves its contents.",
        "Client shall provide truthful qualification and disciplinary information, obtain required examinations, fingerprints and verifications, sign applicant certifications and pay approved board and verification charges directly or reimburse authorized charges.",
        "Scribe Align does not issue licenses, make legal eligibility determinations or represent applicants in disciplinary matters. Approval and processing time are controlled by the licensing authority. Renewal monitoring, additional jurisdictions and contested proceedings require a separate scope."
    ),
    "compliance_training": (
        "Scribe Align shall prepare and deliver the training program described below, including agreed subjects, audience, delivery method, instructional materials and attendance or completion records. The project is complete when the specified sessions and materials have been delivered.",
        "Scribe Align shall base the curriculum on the identified requirements and Client information, present practical administrative guidance, record the material version and refer unresolved legal or clinical questions to Client's qualified advisers.",
        "Client shall identify relevant operations, risks, policies and jurisdictions; ensure staff participation; review organization-specific materials; and implement, document and enforce its own compliance program and corrective measures.",
        "Training does not constitute legal advice, accreditation, certification of HIPAA compliance or a comprehensive audit. Continuing updates, retraining, policy implementation and professional continuing education credit are excluded unless expressly included."
    ),
    "website_development": (
        "Scribe Align shall design, develop and deliver the website identified in the approved specification, including listed pages, features, integrations, revision rounds and handover materials. Work begins after Client supplies required content, access and approvals.",
        "Scribe Align shall implement the agreed design and security requirements, test the specified functions, provide a review version and correct reproducible material failures to meet the approved specification. Deployment requires Client's written approval and shall include delivery of agreed administrative access and source assets.",
        "Client shall provide rights-cleared content, approve clinical and advertising statements, obtain domains and approved hosting, review deliverables promptly and supply privacy notices and consent language approved for its operations.",
        "Patient intake, payment processing, portals, tracking technology and PHI handling are excluded unless expressly specified with appropriate security and contractual arrangements before implementation. Hosting, recurring maintenance, third-party licenses and guaranteed search ranking are excluded unless listed. Accessibility deliverables must be expressly defined; no general legal compliance certification is provided."
    ),
    "consultancy": (
        "Scribe Align shall perform the defined advisory project, review the agreed information and deliver the assessment, recommendations or workshop materials identified below. Project objectives, assumptions, deliverables and completion criteria are limited to the stated service parameters.",
        "Scribe Align shall exercise reasonable professional care, disclose material information gaps, document principal findings and discuss the deliverable with Client. Additional analysis outside the agreed objectives requires a written change order.",
        "Client shall provide complete and accurate information and timely access to responsible personnel, review the deliverable and make all implementation and business decisions.",
        "This engagement does not include clinical practice, legal or tax advice, an independent audit or a guarantee of financial or regulatory outcomes. Implementation and continuing support are excluded unless expressly stated."
    ),
    "mips_consultancy": (
        "Scribe Align shall support the identified MIPS performance year, reporting entity and submission pathway by reviewing available eligibility and measure information, preparing an agreed reporting plan, checking supplied data for administrative completeness and assisting with authorized submission tasks.",
        "Scribe Align shall document material assumptions, identified data gaps and relevant published submission deadlines, preserve agreed submission evidence and obtain Client approval before any submission or attestation made on Client's behalf.",
        "Client shall confirm participation status and reporting elections, maintain source evidence, validate clinical and performance data, provide access and approvals before applicable deadlines and make required authorized attestations. Client shall retain records needed to support an audit.",
        "CMS determines eligibility, scoring, adjustments and relief. Scribe Align does not guarantee a score, incentive, avoided penalty or successful exception. Registry charges, data extraction development, post-submission audits and appeals are excluded unless expressly included."
    ),
    "coding_consultancy": (
        "Scribe Align shall review the defined sample or coding issue, evaluate documentation against applicable coding guidance and provide written findings, recommendations and any specified education session. The service parameters shall identify sample size, specialties, dates of service and deliverables.",
        "Scribe Align shall identify the records reviewed, explain material coding observations and refer documentation ambiguity to the responsible clinician. It shall distinguish sample findings from any broader conclusions and recommend corrections only when supported by the records.",
        "Client shall provide complete authenticated records and relevant payer instructions, supply clinician clarification, decide and implement corrective actions and perform any required payer reporting or refunds.",
        "The project does not certify all claims or records, determine medical necessity independently, provide legal opinions or include recurring coding operations. Findings outside the reviewed sample require further analysis before broader application."
    ),
    "other": (
        "Scribe Align shall perform only the administrative or advisory services, deliverables and completion criteria expressly described in the service parameters below. No unstated service is included by implication.",
        "Scribe Align shall perform the defined work with reasonable care, maintain appropriate work records, identify material dependencies and deliver the agreed work product for Client review.",
        "Client shall provide the information, access, decisions and approvals identified in the service parameters and retain responsibility for its clinical, legal and business decisions.",
        "Clinical care, legal representation and activities requiring an unheld professional license are excluded. PHI access requires a valid BAA and an authorized purpose before performance. Additional deliverables or obligations require a written agreement."
    ),
}

def block(kind, text):
    return {"type": kind, "text": str(text)}

def _clean(value):
    return str(value or "").strip()

def _date(value):
    parsed = date.fromisoformat(_clean(value)[:10])
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"

def _number(value, default="0"):
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except InvalidOperation:
        raise ValueError("A pricing field is not a valid number.")

def _money(value):
    return f"${_number(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"

def _plain_number(value):
    return format(_number(value).normalize(), "f")

def _paragraphs(text):
    return [block("p", x.strip()) for x in _clean(text).splitlines() if x.strip()]

def build_baa(data, contract_id):
    """Fixed BAA language; deliberately does not inspect services or pricing."""
    client = _clean(data["client_name"])
    effective = _date(data["effective_date"])
    address = _clean(data["address"])
    provider_address = _clean(data.get("provider_address")) or DEFAULT_ADDRESS
    state = _clean(data.get("governing_state")) or "Nevada"
    b = [block("title", "BUSINESS ASSOCIATE AGREEMENT"),
         block("p", f"Agreement reference: {contract_id} | Effective Date: {effective}"),
         block("p", f'This Business Associate Agreement ("Agreement") is effective {effective} between {client}, with its address at {address} ("Covered Entity"), and {ENTITY}, with its address at {provider_address} ("Business Associate"). Each is a "Party" and together they are the "Parties."'),
         block("p", "The Parties enter into this Agreement to govern protected health information handled by Business Associate on behalf of Covered Entity in performing separately authorized services. In consideration of their mutual promises, the Parties agree as follows.")]
    sections = [
      ("1. Definitions and Application", [
       ("1.1 HIPAA Rules", "The HIPAA Rules mean the Privacy, Security, Breach Notification and Enforcement Rules at 45 CFR Parts 160 and 164, as amended and applicable. Terms defined in those rules, including Breach, Designated Record Set, Individual, Required By Law, Secretary, Security Incident, Subcontractor and Unsecured Protected Health Information, have their regulatory meanings."),
       ("1.2 Protected Information", "Protected health information or PHI means the protected health information Business Associate creates, receives, maintains or transmits on behalf of Covered Entity. Electronic PHI is PHI in electronic form. This Agreement applies to that PHI in every medium and does not authorize a service or disclosure prohibited by applicable law.")]),
      ("2. Permitted Uses and Disclosures", [
       ("2.1 Authorized Services", "Business Associate may use or disclose PHI only to perform services authorized in a written agreement with Covered Entity, as expressly permitted by this Agreement, or as Required By Law. Except for the management and administration uses and disclosures permitted below, it shall not use or disclose PHI in a manner that would violate the Privacy Rule if done by Covered Entity."),
       ("2.2 Administration and Legal Responsibilities", "Business Associate may use PHI for its proper management and administration or to carry out its legal responsibilities. Disclosure for these purposes is permitted only if Required By Law or after obtaining reasonable written assurances that the recipient will protect confidentiality, limit further use or disclosure to the stated purpose or legal requirements, and report any known breach of confidentiality to Business Associate."),
       ("2.3 Limitations", "Business Associate shall apply the minimum necessary standard when that standard applies under the HIPAA Rules, including its recognized exceptions. No sale, independent commercial use, data aggregation across clients, de-identification for separate use, product development or model training is authorized by this Agreement. Marketing, fundraising or other activities requiring individual permission require Covered Entity's written instruction and every legally required individual authorization or other permission before the activity begins."),
       ("2.4 Legal Process", "To the extent legally permitted, Business Associate shall promptly notify Covered Entity of a demand for PHI, permit a reasonable opportunity to seek protection and disclose only what the applicable legal requirement compels. It shall not voluntarily waive protections or expand a demand on Covered Entity's behalf.")]),
      ("3. Safeguards and Subcontractors", [
       ("3.1 Security Program", "Business Associate shall use appropriate administrative, physical and technical safeguards and comply with the Security Rule with respect to electronic PHI. It shall maintain risk analysis and risk management, workforce authorization and training, incident procedures, contingency arrangements, appropriate access controls, audit controls, integrity and transmission safeguards, and required documentation."),
       ("3.2 Contractual Security Measures", "Business Associate shall restrict access to authorized persons with a work-related need, use unique accounts and multi-factor authentication where supported for remote PHI access, protect electronic PHI in transit and at rest with appropriate encryption, and promptly revoke unnecessary access. Where a system cannot support a specified measure, the Parties shall document an appropriate alternative before use. PHI shall not be placed in personal accounts, unapproved tools or public systems."),
       ("3.3 Subcontractors", "Before a subcontractor creates, receives, maintains or transmits PHI on its behalf, Business Associate shall enter into a written agreement imposing the same applicable restrictions, conditions and requirements, including Security Rule obligations. Business Associate shall address known material violations through reasonable steps to cure or end them and terminate the relationship if those steps fail and termination is feasible. Business Associate remains responsible for its contractual performance."),
       ("3.4 Delegated Duties", "To the extent Business Associate performs an obligation of Covered Entity under the Privacy Rule, it shall comply with the requirements applicable to that obligation. It shall also comply with applicable standard transaction requirements when conducting such transactions on Covered Entity's behalf.")]),
      ("4. Incidents and Breach Response", [
       ("4.1 Initial Notice", "Business Associate shall report any use or disclosure not permitted by this Agreement and any Security Incident of which it becomes aware without unreasonable delay and no later than five business days after discovery. A Breach of Unsecured PHI shall be reported within that same contractual period and in all events within the period required by 45 CFR 164.410. The statutory outside limit of 60 calendar days does not extend the five-business-day contractual deadline."),
       ("4.2 Discovery and Content", "Discovery occurs when the event is known, or would have been known through reasonable diligence, to Business Associate, including knowledge attributed under the HIPAA Rules. Initial notice shall include available facts, affected persons and data, relevant dates, containment measures and a response contact. Incomplete information shall not delay initial notice. Additional information required for Covered Entity's notifications shall be supplied promptly as it becomes available."),
       ("4.3 Cooperation", "Business Associate shall investigate, preserve relevant evidence, mitigate harmful effects to the extent practicable, cooperate in risk assessment and remediation, and provide reasonable status updates. Covered Entity shall direct notices to individuals, regulators and media unless law independently requires Business Associate to notify or the Parties agree otherwise in writing. Neither Party may delay a legally required notice. Notification and cooperation do not constitute an admission of liability.")]),
      ("5. Individual Rights and Oversight", [
       ("5.1 Access", "Within ten business days after Covered Entity's request, or a shorter reasonable period needed for a legal deadline, Business Associate shall make PHI in a Designated Record Set available to Covered Entity or, as directed, the Individual, in the requested form and format when required by law. It shall forward a request received directly from an Individual to Covered Entity within three business days and shall not independently deny access."),
       ("5.2 Amendment", "Business Associate shall make PHI available for amendment and incorporate amendments directed or agreed to by Covered Entity as necessary to satisfy 45 CFR 164.526. It shall respond within ten business days of direction, or a shorter reasonable period needed to meet a legal deadline."),
       ("5.3 Accounting", "Business Associate shall record disclosures subject to accounting and supply the information needed for Covered Entity to comply with 45 CFR 164.528 within ten business days of request. Required information shall be retained for the applicable six-year accounting period. No obligation to account for an exempt disclosure is created solely by this provision."),
       ("5.4 Government Review", "Business Associate shall make its internal practices, books and records relating to PHI available to the Secretary for determining compliance with the HIPAA Rules. It shall furnish Covered Entity reasonable information concerning compliance with this Agreement, subject to safeguards for unrelated confidential information and privilege, without restricting lawful governmental access.")]),
      ("6. Covered Entity Obligations", [
       ("6.1 Instructions and Permissions", "Covered Entity shall provide lawful instructions, obtain required permissions and promptly notify Business Associate of privacy notice limitations, changes or revocations of permission, and agreed or legally required restrictions that affect the services. Covered Entity shall not request a use or disclosure that would violate applicable law and shall transmit only information appropriate to the authorized purpose."),
       ("6.2 Specially Protected Records", "Covered Entity shall identify records subject to additional confidentiality requirements, including 42 CFR Part 2 when applicable, and provide the necessary consent, restrictions and instructions. The Parties shall establish required additional protections before Business Associate receives such records. This Agreement alone does not supply a missing consent or authorize a prohibited use.")]),
      ("7. Term and Termination", [
       ("7.1 Duration", f"This Agreement begins on {effective} and continues while Business Associate handles PHI on Covered Entity's behalf. Termination of a services agreement does not terminate protections for retained PHI."),
       ("7.2 Material Breach", "Covered Entity may terminate this Agreement and affected services for Business Associate's material violation. If a cure is feasible, Covered Entity may allow up to fifteen calendar days after written notice to cure; it may require an earlier protective measure or terminate immediately when cure is not feasible or continued access presents a material privacy or security risk. These remedies do not limit obligations imposed directly by law."),
       ("7.3 Return or Destruction", "Upon termination, Business Associate shall, where feasible, return or securely destroy all PHI, including subcontractor copies, at Covered Entity's direction, retain no copies and confirm completion in writing within thirty calendar days. It shall provide a reasonably usable export when return is directed and shall not withhold PHI because of a fee dispute."),
       ("7.4 Infeasible Return or Destruction", "If return or destruction is infeasible, Business Associate shall identify the retained information and reasons in writing, continue all protections, limit uses and disclosures to the purposes making return or destruction infeasible, and return or destroy it when feasible. Retained backups shall remain protected and shall not be restored for an unrelated purpose. These obligations survive for as long as PHI is retained.")]),
      ("8. General Provisions", [
       ("8.1 Priority and Amendment", "This Agreement controls over inconsistent terms of any services agreement on PHI and HIPAA matters. It does not replace unrelated commercial agreements. The Parties shall cooperate in signed amendments required by applicable law; no amendment may reduce a mandatory legal protection. References to law mean the applicable law as amended, and ambiguity shall be resolved to permit compliance."),
       ("8.2 Notices", f"Notices shall be sent to the other Party's address stated above and to its designated privacy or contract contact. Covered Entity contact: {_clean(data.get('client_email')) or 'the authorized signatory at the stated address'}. Business Associate contact: {_clean(data.get('provider_email')) or 'Chief Executive Officer at the stated address'}. Incident notices shall use a prompt written method with follow-up to confirm receipt. Either Party may update its contact by written notice."),
       ("8.3 Applicable Law and Execution", f"Federal law governs HIPAA obligations. The laws of {state}, excluding conflict of law rules, govern remaining contractual issues to the extent permitted by law. Invalidity of a provision does not invalidate the remainder. No third-party contractual beneficiary is created, and no statutory right is limited. Authorized representatives may execute counterparts and lawful electronic signatures, which together form one instrument.")]),
    ]
    for heading, clauses in sections:
        b.append(block("h1", heading))
        for subtitle, paragraph in clauses:
            b += [block("h2", subtitle), block("p", paragraph)]
    b.append(block("signature", f"The undersigned agree to the foregoing Business Associate Agreement, reference {contract_id}, effective {effective}, between {client} and {ENTITY}. This execution page forms part of that Agreement and shall remain attached to its complete terms."))
    return b

def _service_record(entry):
    base = CATALOG_BY_ID.get(entry.get("id"), {})
    return {**base, **entry}

def _fee_description(s):
    model = s["model"]
    rate = _number(s.get("rate"))
    quantity = _number(s.get("quantity"), "1")
    if model == "percentage":
        return f"{_plain_number(rate)}% of Eligible Net Collections; invoiced monthly in arrears."
    if model in ("fte", "pte"):
        hours = _plain_number(s.get("hours", 40 if model == "fte" else 20))
        return f"{_money(rate)} per {model.upper()} per month x {_plain_number(quantity)} positions = {_money(rate * quantity)} per month. Each position includes {hours} scheduled hours per week."
    if model == "unit":
        unit = _clean(s.get("unit")) or "completed case"
        return f"{_money(rate)} per {unit}; monthly invoice equals that rate multiplied by documented completed units. Planning volume: {_plain_number(quantity)} units, without a minimum purchase commitment."
    if model in ("fixed", "consultancy"):
        return f"{_money(rate)} per month for the expressly described scope."
    total = (rate * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if model == "two_installments":
        first = _number(s.get("installment_percent"), "50")
        second = Decimal("100") - first
        first_amount = (total * first / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        second_amount = total - first_amount
        milestone1 = _clean(s.get("milestone1")) or "execution and commencement"
        milestone2 = _clean(s.get("milestone2")) or "delivery of the agreed final work product or completion milestone"
        return f"Total project fee: {_money(total)} ({_money(rate)} x {_plain_number(quantity)} projects or application units). First installment: {_plain_number(first)}% ({_money(first_amount)}); milestone: {milestone1}. Second installment: {_plain_number(second)}% ({_money(second_amount)}); milestone: {milestone2}. Any cent-rounding remainder is included in the second installment. These are the only two scheduled project invoices."
    return f"Total project fee: {_money(total)} ({_money(rate)} x {_plain_number(quantity)} projects or application units); one invoice upon delivery of the agreed final work product or completion milestone."

def build_services(data, contract_id):
    client = _clean(data["client_name"])
    effective = _date(data["effective_date"])
    state = _clean(data.get("governing_state")) or "Nevada"
    notice = int(data.get("notice_days", 30))
    payment = int(data.get("payment_days", 15))
    cure = int(data.get("cure_days", 30))
    selected = [_service_record(s) for s in data.get("services", [])]
    selected += [_service_record({"id": "custom", **s}) for s in data.get("custom_services", [])]
    has_recurring = any(s.get("category", "on_demand") == "recurring" for s in selected)
    has_projects = any(s.get("category", "on_demand") == "on_demand" for s in selected)
    has_percentage = any(s.get("model") == "percentage" for s in selected)
    b = [block("title", "SERVICES AGREEMENT"),
         block("p", f"Agreement reference: {contract_id} | Effective Date: {effective}"),
         block("p", f'This Services Agreement ("Agreement") is effective {effective} between {ENTITY}, with its address at {_clean(data.get("provider_address")) or DEFAULT_ADDRESS} ("Scribe Align"), and {client}, with its address at {_clean(data["address"])} ("Client"). Each is a "Party" and together they are the "Parties." The Parties agree as follows.'),
         block("h1", "1. Engagement and Contract Documents"),
         block("p", "Client engages Scribe Align solely for the services expressly selected in Section 3. The service descriptions, pricing and any signed special terms form part of this Agreement. Unselected services are not included. Operational instructions may clarify an existing scope but may not change fees, legal obligations or deliverables without written approval by authorized representatives."),
         block("p", "This is a separate engagement for its stated services. It does not terminate, replace or amend a prior agreement for other services. If the same work is already covered by another agreement, the Parties shall resolve the overlap in a signed writing before performance or invoicing, and the same work shall not be charged twice."),
         block("h1", "2. Term and Service Administration"),
         block("p", f"This Agreement begins on {effective}, continues month to month and automatically renews for successive monthly periods unless either Party gives at least {notice} calendar days' written notice of termination. Termination takes effect on the date stated in the notice, which shall not be earlier than expiration of the notice period. No new signature is required for renewal.")]
    if has_projects:
        b.append(block("p", "Renewal preserves the contractual framework for an unfinished or newly authorized project. It does not renew a completed project, authorize additional units or create a recurring fee. Each on-demand project has only the one or two scheduled invoices stated for that project. A new project requires written authorization describing its scope and fee."))
    b.append(block("p", "The Parties shall designate operational contacts and agree on access, initial work allocation and delivery schedules. A deadline dependent on Client information or third-party action shall be adjusted reasonably for a documented delay, after prompt notice describing its effect. No delay excuses either Party's independent legal duties."))
    b.append(block("h1", "3. Selected Services and Fees"))
    b.append({"type": "table", "headers": ["Selected service", "Billing basis"], "rows": [[s["name"], _fee_description(s)] for s in selected]})
    b.append(block("p", "All fees are in United States dollars. The table and the service terms below shall be read together. Third-party charges are payable only if approved in writing in advance and supported by reasonable documentation; they are not included in the fee unless expressly stated."))
    for idx, s in enumerate(selected, 1):
        prefix = f"3.{idx}"
        b.append(block("h2", f"{prefix} {s['name']}"))
        scope = SCOPES.get(s.get("id"), SCOPES["other"])
        labels = ("Scope of Work", "Scribe Align Responsibilities", "Client Responsibilities", "Limitations and Exclusions")
        paragraphs = list(scope)
        if s.get("scope"):
            paragraphs[0] = _clean(s["scope"])
        if s.get("responsibilities"):
            paragraphs[2] = _clean(s["responsibilities"])
        if s.get("exclusions"):
            paragraphs[3] = _clean(s["exclusions"])
        for number, (label, para) in enumerate(zip(labels, paragraphs), 1):
            b += [block("h3", f"{prefix}.{number} {label}"), block("p", para)]
        if _clean(s.get("details")):
            b.append(block("h3", f"{prefix}.5 Agreed Service Parameters"))
            b.extend(_paragraphs(s["details"]))
        b.append(block("p", "Fee and invoicing: " + _fee_description(s)))
    b += [block("h1", "4. Invoicing and Payment"),
          block("p", f"Scribe Align shall provide itemized invoices identifying the services, applicable period, rate, quantity or calculation and approved charges. Client shall pay undisputed amounts within {payment} calendar days after receipt. Payment shall be by a method agreed by the Parties; this Agreement does not itself authorize a debit from Client's account."),
          block("p", "Client shall promptly notify Scribe Align of a good-faith invoice dispute, identify the amount and reasons, and pay the undisputed balance when due. Scribe Align shall provide reasonably requested supporting records, and the Parties shall work in good faith to resolve the dispute. A resolved balance is due within ten calendar days after resolution. No late charge is imposed by this Agreement.")]
    if has_recurring:
        b.append(block("p", "Recurring services are invoiced monthly in arrears. Fixed and staffing fees for a partial first or final month are prorated by the calendar days of service in that month. Approved staffing allocations and weekly hours determine capacity; they do not promise a particular result. Unprovided scheduled staffing hours shall be rescheduled by agreement or credited proportionately. Overtime, additional positions and fee increases require prior written approval."))
    if has_projects:
        b.append(block("p", "A project milestone is satisfied by delivery of the specified work or completion of the stated activity, not by an independent agency's approval, unless the service parameters expressly make approval the milestone. Client shall identify any material nonconformity within ten business days of delivery. Scribe Align shall correct substantiated nonconformities within a reasonable period without additional charge. A timely, specific dispute preserves Client's right to withhold the reasonably disputed amount while it is resolved."))
    if has_percentage:
        b += [block("h2", "4.1 Eligible Net Collections"),
              block("p", "Eligible Net Collections means amounts actually received and cleared in Client-controlled accounts during the billing period, attributable solely to claims or accounts expressly assigned to and serviced by Scribe Align under the applicable selected service. It excludes refunds, recoupments, reversals, duplicate payments, overpayments identified for return, taxes, interest, penalties, unrelated receipts, unassigned accounts, loans, grants, capitation and incentive payments unless an identified category is expressly included by a lawful signed amendment. Contractual adjustments, billed charges and uncollected balances are not collections."),
              block("p", "Refunds and reversals relating to previously charged collections shall reduce the next invoice; any unapplied credit shall be refunded within thirty calendar days after final reconciliation. If more than one selected percentage service touches the same receipt, only one percentage fee applies unless the Parties expressly agree to distinct, lawful compensation for separate work. Client shall provide remittance and collection records reasonably needed to verify the calculation."),
              block("p", "Payer and patient payments shall be made to accounts owned and controlled by Client. Scribe Align shall invoice Client separately and shall not take assignment of claims, receive Medicare payments as Client's payment agent, endorse Client's checks or withdraw its fees from collections. If a proposed compensation method is prohibited by applicable payer, fee-splitting or other law, the affected work shall pause until the Parties agree in writing to a lawful fee; no substitute rate is imposed automatically."),
              block("p", "Percentage fees accrue only on Eligible Net Collections received on or before the effective termination date. There is no post-termination collection tail unless a separate signed transition arrangement expressly defines the accounts, period and lawful fee. Accrued fees and later refund adjustments remain subject to reconciliation.")]
    b += [block("h1", "5. Performance Standards and Client Cooperation"),
          block("p", "Scribe Align shall perform with reasonable skill, care and diligence using personnel suited to the assigned work, comply with laws applicable to its activities, supervise its personnel and maintain reasonable continuity arrangements. It shall promptly notify Client of material errors or obstacles and correct errors attributable to its work within a reasonable period. Scribe Align shall remain responsible for subcontracted performance."),
          block("p", "Client shall supply timely and accurate information, lawful access, necessary approvals and a responsive decision-maker, and shall maintain its required licenses and authority. Each Party shall promptly inform the other of a material compliance issue affecting the work. Neither Party shall direct, submit, conceal or knowingly facilitate a false or unsupported statement or claim. A disputed clinical or legal question shall be referred to an appropriately qualified person before the affected action proceeds."),
          block("p", "The relationship is that of independent contractors. Neither Party may bind the other except through a specific written authorization. Scribe Align controls and compensates its personnel and is responsible for its applicable employment obligations. Client retains professional judgment, patient care and control of its practice. No referral, minimum patient volume or purchase of other services is required by this Agreement."),
          block("h1", "6. Confidentiality and Information Security"),
          block("p", "Each Party shall use the other's nonpublic business information solely to perform or administer this Agreement, protect it with reasonable care, and disclose it only to authorized persons bound to confidentiality or as law requires. This duty does not cover information lawfully public, previously known without restriction, independently developed or lawfully received from a third party. Legally compelled disclosure shall be limited to what is required, with advance notice where permitted."),
          block("p", "Before Scribe Align creates, receives, maintains or transmits PHI as Client's business associate, the Parties shall have an executed Business Associate Agreement in force. This Services Agreement alone is not a substitute. The BAA controls PHI use, disclosure, safeguards, individual rights, incidents, subcontractors and return or destruction. No PHI shall be used for independent advertising, sale, product development or model training without a separately documented lawful authorization and agreement."),
          block("p", "The Parties shall use authorized systems, limit access to required personnel and promptly report a material compromise of confidential information to the other. PHI events are governed by the BAA. Client shall identify additional restrictions on sensitive records before access. Confidential information and PHI shall not be withheld to secure payment."),
          block("h1", "7. Records and Work Product"),
          block("p", "Client retains ownership of its records, accounts, data and materials. Upon payment of the fees attributable to a deliverable, Client owns the specifically commissioned final work product to the extent Scribe Align may lawfully transfer it. Scribe Align retains pre-existing tools, templates, methods and general know-how, and grants Client a perpetual, nonexclusive license to use any such material embedded in a paid deliverable for Client's internal business purposes. Third-party components remain subject to disclosed license terms."),
          block("p", "Client remains the custodian of its official patient and practice records and shall maintain legally required originals and retention systems. Scribe Align shall provide reasonable exports of Client records and agreed work status on request or termination. No provision requires retention or destruction contrary to law or the BAA. Each Party shall retain only the information necessary for its lawful purposes."),
          block("h1", "8. Termination, Suspension and Transition"),
          block("p", f"Either Party may terminate for the other's material breach if it remains uncured {cure} calendar days after written notice describing the breach. Immediate termination is permitted for an incurable material breach, fraud, unlawful performance or a material ongoing threat to protected information. Any stricter BAA termination right remains effective. Termination for convenience is governed by Section 2."),
          block("p", "Scribe Align may suspend affected services for a material undisputed overdue balance only after giving at least ten calendar days' written notice and an opportunity to pay. Either Party may restrict activity reasonably necessary to prevent unlawful conduct or a material security threat. Suspension shall be proportionate, shall not obstruct access to Client records and shall include reasonable coordination to avoid foreseeable disruption to patient care or filing obligations."),
          block("p", "At termination, Client shall pay fees earned for conforming work performed through termination and previously approved noncancelable third-party costs. Unearned prepaid amounts shall be refunded within thirty calendar days after reconciliation. No fee is automatically accelerated solely because of termination. For a partly completed fixed project, the Parties shall reconcile the reasonable proportion completed against the agreed total fee, supported by work records, without exceeding that total."),
          block("p", "Scribe Align shall provide Client with a reasonable status summary, assigned work records and agreed deliverables, identify known pending deadlines and cooperate in orderly transfer. Standard record export and return are included; additional transition work requires written scope and fees. Access shall be revoked in coordination with secure transfer, and PHI shall be handled as required by the BAA."),
          block("h1", "9. Responsibility and Remedies"),
          block("p", "Each Party is responsible for loss to the extent caused by its breach, negligence, willful misconduct or violation of applicable law, subject to applicable defenses and duties to mitigate. Each shall indemnify the other against a third-party claim to that same extent, including reasonable defense costs. The protected Party shall promptly give notice, allow reasonable control of the defense with qualified counsel and cooperate. A settlement imposing an admission, nonmonetary duty or unreimbursed payment on the protected Party requires its written consent, which shall not be unreasonably withheld."),
          block("p", "Neither Party guarantees the conduct of a payer, regulator, platform, applicant, patient or other independent third party. No particular collection, license, enrollment, ranking or financial result is warranted unless expressly promised in signed special terms. These limitations do not excuse failure to perform the agreed services with reasonable care or limit liability that applicable law does not permit the Parties to limit."),
          block("p", "The Parties shall first refer a contractual dispute to authorized representatives for good-faith discussion for fifteen calendar days after written notice. This process does not prevent urgent protective relief, a legally required report or timely filing to preserve a claim. There is no mandatory arbitration or waiver of statutory remedies under this Agreement."),
          block("h1", "10. Notices and General Terms"),
          block("p", f"Contract notices shall be written and delivered personally, by tracked mail or courier to the addresses above, or by email with acknowledged receipt. Client notice email: {_clean(data.get('client_email')) or 'as subsequently designated in writing'}. Scribe Align notice email: {_clean(data.get('provider_email')) or 'as subsequently designated in writing'}. Routine silence or an automated delivery message alone is not acknowledgment of a termination or breach notice. A Party may change its notice details by written notice."),
          block("p", f"The laws of {state}, excluding conflict of law rules, govern this Agreement, subject to controlling federal law. Any court proceeding shall be brought in a court of competent jurisdiction in that state unless applicable law requires otherwise. Each Party remains responsible for mandatory laws applicable where its activities occur."),
          block("p", "Neither Party may assign this Agreement without the other's written consent, except to a successor in a genuine merger or sale of substantially all relevant assets that assumes the obligations and does not materially impair performance or information protections. An assignment does not release accrued obligations. No third-party contractual beneficiary is intended."),
          block("p", "A Party affected by an event beyond its reasonable control shall promptly notify the other, mitigate its effects and resume performance as reasonably possible. This provision does not excuse accrued payment, confidentiality or information protection duties, or a failure to maintain reasonable continuity safeguards. If material interruption continues for thirty calendar days, either Party may terminate affected services without an additional termination charge."),
          block("p", "This Agreement is the entire agreement for its stated scope, subject to the separate BAA and the preservation of unrelated engagements in Section 1. Amendments and waivers must be in writing and approved by authorized representatives. A waiver on one occasion is not a continuing waiver. If a provision is unenforceable, the remaining provisions continue to the extent lawful. Accrued payment, confidentiality, information protection, ownership and provisions needed to resolve accrued claims survive termination. Counterparts and lawful electronic signatures form one instrument.")]
    if _clean(data.get("special_terms")):
        b.append(block("h1", "11. Special Terms"))
        b.extend(_paragraphs(data["special_terms"]))
        b.append(block("p", "These special terms modify a general commercial provision only to the extent expressly stated. They do not reduce mandatory legal duties or protections required by the BAA."))
    b.append(block("signature", f"The undersigned agree to the foregoing Services Agreement, reference {contract_id}, effective {effective}, between {client} and {ENTITY}, including only the selected services and fees stated in Section 3. This execution page forms part of that Agreement and shall remain attached to its complete terms."))
    return b
