# TFSC Agreements 5.0: architecture plan

This plan answers the feedback document (signatures, UI/UX, signing order, completion, emails, dashboard, reports,
activity log, copyright and help). Every point is listed with its design. The rules below hold for all of it.

## Ground rules

1. **Existing data is kept.** The update is one additive migration (`0004_premium_portal`): new tables and new nullable
   columns only. Nothing is dropped or rebuilt. Users, clients, agreements, envelopes, mailboxes and audit history carry
   over. The update is tested against a copy of a 4.2 installation that holds data.
2. **The server decides.** Every new screen calls an API that checks the role (User, Admin, Super Admin), the team
   (an Admin sees their own users) and the company permissions. Each rule has a test that tries to break it.
3. **No claim the system cannot back up.** Wording about security describes what the portal really does: an encrypted
   connection, a tamper-evident audit trail, access codes and virus scanning. The HIPAA line can be edited by the
   Super Admin, and counsel reviews it (see LEGAL_REVIEW.md). The verified tick in Gmail needs DNS records and a
   certificate the company buys. The portal checks the DNS records and explains each step. It does not pretend to
   provide the tick itself.
4. **Same deployment.** Same ZIP layout, same `agreements.sh update`, same Docker images, same backups.

## 1. Company signatures (feedback item 1)

**Cause found.** The company signature only appeared when image files were installed on the server from the command
line. The upload box was hidden in step 5 of the builder and shown only to Super Admins. On the reviewed server no
image was installed, so the company side was blank.

**Design.**
- New table `company_profiles`, one row per company: signatory name, title, the signature as a PNG, how the signature
  was made (uploaded / drawn / style), who changed it and when. It is stored in the database, so backups include it.
- **Admin › Company settings** (Super Admin only). Each company card shows the logo, the signatory and the
  signature exactly as it will sit on the signature line. The signature can be changed three ways:
  - upload a photo or scan, which the server trims and makes transparent automatically;
  - draw it;
  - type it and pick a style.
- **Which signature is used.** Generation uses the company settings first. If none is saved, it falls back to an image
  installed with `agreements.sh signatures`. If neither exists, the builder shows a clear warning before you generate.
- **Size and position.** The signature is trimmed to its ink and sized up to 2.3 × 0.96 in (space above the rule makes room for it), times the company's signature size
  (`company_profiles.signature_scale`, 30–100 %; defaults in `entities.py`: 60 % for Naeem Abbas, 85 % for Danish
  Ather, approved by the owner on previews of all nine agreements). Its lower edge sits on the
  signature line, so it reads clearly and is never tiny.
- **Defaults.** Naeem Abbas, Chief Executive Officer, for Scribe Align LLC and Extended Health Services Inc. Danish
  Ather, Chief Operating Officer, for 24-7 Consultancy LLC.
  - The feedback document names "Naeem Abbas … Chief Operating Officer" for 24-7. We read that as a copy error,
    because item 1 itself names Danish Ather as the second signatory.
  - Either way, the Super Admin can set the name, title and signature for each company in seconds.
- Envelopes started from generated agreements carry the company signature already printed on the company side.

## 2. Signing experience (signers)

| Feedback | Design |
|---|---|
| Signatures look bold, big and unprofessional | Stamps redrawn in the DocuSign proportion: a thin bracket, "Signed by:" and the signature ID in small grey text, and ink at most 24 pt high in a 170 × 42 pt field. Initials get a small framed stamp, at most 16 pt of ink. Typed signatures are rendered thin and trimmed, so they scale cleanly. |
| More styles | 14 signature styles from OFL-licensed script fonts, served from the portal itself (no outside font service). |
| Upload / choose / draw, signature **and** initials separately | The Adopt dialog has three tabs: **Style**, **Draw** and **Upload**. Each tab has a Signature side and an Initials side, so a signer can, for example, draw the signature and pick a style for the initials. Uploaded images are trimmed and their white background removed in the browser. |
| Current date pulled automatically | Date-signed fields show today's date (in the company's time zone) as soon as the page opens, and are stamped at Finish. |
| Link must not expire unless the sender expires it | **No expiration by default.** The sender can set one in Settings. Completed-document links stay active; the sender can turn them off on the envelope page ("Turn off recipient links"). Envelopes already waiting that carry the old 30-day default have that expiry removed by the update. |
| No bad experience for signers | Premium signing page in the company's colours, with a progress bar of required fields, a "Next" guide, zoom, page thumbnails, Finish later, mobile layout and clear help. |

## 3. Sender experience (DocuSign-style)

**App shell.** A white top bar with tabs: **Home · Agreements · Templates · Clients · Create agreement · Reports · Admin**
(Admin is for Admins and above). On the right are Help (?) and the user menu. The left builder sidebar is removed; the
builder's five steps become a horizontal stepper inside Create agreement. Browser Back/Forward work (hash routes).
The design system is one stylesheet (`ui.css`): Inter typeface, navy/blue palette, 12 px radii, soft shadows,
consistent buttons, chips and tables, plus a mobile layout.

**Home.** The equivalent of DocuSign Home:
- a welcome banner with a **Start** menu (Send an envelope, Create an agreement, Use a template, Bulk send);
- **Agreement status, last 6 months**: Action required, Waiting for others, Expiring soon, Completed. Each count opens
  that list;
- a **Sent / Pending signature / Completed / Completion rate** strip, which can be filtered by company;
- recent agreement activity (viewed, signed, completed).

**Agreements.** A folder sidebar:
- Action required, Waiting for others, Expiring soon, Completed, Drafts, Declined, Voided, Expired, All sent;
- a table with subject, recipients, status icon, last change and row actions (Sign, Resend, Download);
- search plus company, client and date filters.

**Send wizard (new envelope).** One page, four sections, the same order as DocuSign:
1. **Add documents.**
   - Drag and drop, or Upload from the PC (PDF, Word, images, text).
   - When a client is chosen, the section also lists the client's **generated agreements** (latest version first) and
     the **files on their profile**. One click loads a document.
2. **Add recipients.**
   - A **Set signing order** checkbox.
   - One card per recipient: order number, Name, Email (with address-book suggestions), and a role dropdown with icons
     (Needs to sign, In person signer, Receives a copy, Needs to view).
   - A **Customize** menu (access code, private message, host for in-person signing) and a delete icon.
   - **+ Add recipient** and **Add me** buttons.
   - The client's signatory is filled in automatically when the envelope is started from a client or an agreement.
3. **Add message.** The email subject and message.
4. **Settings.** Reminders, optional expiration, attaching the completed documents, and permission to reassign.

**Next** opens **Add fields** (the DocuSign tagger):
- a recipient picker in the recipient's colour;
- a field palette with icons;
- the document pages with thumbnails;
- a properties panel.

Fields are **placed automatically** the first time:
- **Generated agreements.** The client's signature, name, title and date go on the signature table. Initials go on
  **every page** in the free space above the footer, on the opposite side from the page number.
- **Uploaded documents.** The text is scanned for "Signature", "Sign here", "Name", "Title", "Date" and "Initials"
  labels, and fields are placed beside them. If none are found, a signature block goes in the free space at the end of
  the last page.
- Every page gets initials in a free corner that does not cover text.

Every field can be moved, resized, deleted, duplicated or added. **Auto-place again** re-runs the placement for the
selected recipient.

**Envelope details.**
- A header with the status and a progress bar ("2 of 3 signed").
- The primary actions, plus a More menu: Correct, Void, Copy, Save as template and Turn off links.
- A recipient timeline (sent, viewed, signed, with times and IP), document cards, then tabs for History (the verified
  audit trail), Emails and Replies.

**Completion rule (feedback: viewers).**
- An envelope completes when every **signer** has signed. A "Needs to view" recipient never blocks completion.
- With signing order on, a viewer still waits for their turn before signers after them. With it off, everyone receives
  the envelope at once.
- At completion, everyone gets the completed documents and the Certificate of Completion: signers, viewers and copy
  recipients.
- Envelopes already stuck waiting only on a viewer complete automatically after the update.

## 4. Emails (feedback: premium, per-company theme, trust)

- **Company themes from the websites.**
  - Scribe Align: #004B79 with #00AEEF.
  - Extended Health Services: #1C2A39 with #FBCD35.
  - 24-7 Consultancy: #1D4277 with #189BD2.
- **New layout.** A logo header, a coloured hero with the document icon and the headline, a large button, the sender's
  message, a "Secure document" panel (encrypted portal, access by unique link, do not forward), "About this portal",
  then the company address, © notice and help. Table-based with inline styles, so Gmail and Outlook render it.
- **Sender name.** The default is the company name (for example "Scribe Align LLC"), so Gmail's letter avatar is the
  company's initial and not "C".
- **Admin › Email trust.** Checks each company domain over DNS-over-HTTPS:
  - SPF, DKIM (selector configurable), DMARC policy and BIMI;
  - for each, a pass or warning with the exact record to add;
  - the path to the verified tick: DMARC at quarantine or reject, an SVG logo, and a VMC from DigiCert, Entrust or
    GlobalSign;
  - the quick win: set the mailbox's profile photo to the company logo.

## 5. Reports, Insights and Activity log

- **Insights.** Charts and figures:
  - envelopes sent and completed per month;
  - status breakdown;
  - by company;
  - average time to complete;
  - pending signatures by age;
  - top clients.
- **Report builder.**
  - Datasets:
    - Envelopes;
    - Recipients;
    - Generated agreements;
    - Clients;
    - User activity (Admin and above);
    - Users (Admin and above).
  - Filters: date range, company, status, sender and client.
  - A **column chooser**.
  - Output: an on-screen table, **Excel (.xlsx)** with a styled header, frozen row, filters, widths and a "Report
    details" sheet, or **CSV**.
  - Report definitions can be saved and reused.
  - Cells that start with `= + - @` are neutralised, so a spreadsheet never runs a formula from data.
- **Scope.** Users see their own records, Admins see their team's, and the Super Admin sees everything. Company
  permissions always apply.
- **Activity log (Admin and above).** One timeline from two sources:
  - workspace actions: sign-in and sign-out; client created, opened or updated; agreement previewed, generated or
    downloaded (Word/PDF); envelope created, opened, sent, corrected or voided; documents and certificates downloaded;
    reports exported; settings changed;
  - signer actions from the e-signature audit trail: opened, viewed, signed, declined, downloaded.
  - Each entry records who, what, which client, when, the IP address and the browser.
  - Filters: user, action, client and dates. It can be exported to Excel or CSV.
- **Client activity.** The same merged timeline, limited to one client.

## 6. Copyright and Help

- Every screen has the footer "© 2026 … All rights reserved." This covers the portal, sign-in, the signing page, the
  verification page and every email. The Super Admin can set the holder.
- **Help (?)** opens a dialog: "Need help? Please email **muzahir@scribealign.com** with your concern." It includes a
  mailto button and the quick guide. The address can be changed in Company settings.

## 7. Data model changes (migration 0004, additive)

| Change | Purpose |
|---|---|
| table `company_profiles` (entity PK, signatory, title, signature PNG, signature_kind, updated, updated_by) | company signatures in the database |
| table `saved_reports` (id, owner_id, name, definition JSON, created, updated) | saved report definitions |
| `audit.ip`, `audit.user_agent` (nullable) + indexes on `audit.created`, `audit(actor_id, created)` | activity log |
| `settings['workspace']` row (help email, copyright holder, trust line) | workspace settings |
| data: `sent` envelopes with the old default 30-day expiry → no expiry | "links do not expire unless the sender expires them" |

## 8. Module map

| Module | Responsibility |
|---|---|
| `companies.py` | company profiles, signature cleaning (trim and transparency), workspace settings, Email trust DNS checks |
| `insights.py` | Home counts, Insights, report datasets and exports (xlsx/csv), saved reports, Activity log |
| `esign.py` | completion rule, ordering, expiry default, link revocation, library documents, automatic placement endpoint |
| `esign_pdf.py` | smart placement (label detection, free-space initials), new stamps |
| `mailer.py` | premium per-company email layout |
| `index.html`, `ui.css`, `portal.js` | app shell, navigation, routing, help, footer |
| `home.js`, `reports.js`, `admin.js` | Home, Reports/Insights/Activity, Company settings/Email trust |
| `esign.js` | Agreements folders, envelope page, send wizard, Add fields |
| `sign.html/js/css` | signing page and Adopt dialog |

## 9. Verification before delivery

- Unit and permission tests on SQLite and PostgreSQL, including the migration with data.
- A browser run of every screen with screenshots.
- The ZIP installed fresh on Docker, and the ZIP applied as an **update** over a 4.2 installation holding users,
  clients, agreements and envelopes. Every record is checked afterwards.
