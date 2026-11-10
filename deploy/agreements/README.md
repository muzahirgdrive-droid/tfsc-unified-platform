# TFSC Agreements on one server (VPS)

This package installs **TFSC Agreements only** on one Linux server with Docker:
- agreement generation (BAA, Services Agreement, Mutual NDA) on each company's letterhead;
- electronic signature in the style of DocuSign, sent from each company's compliance mailbox with that company's logo
  and colours;
- a profile for every client: details, contacts and every document (generated, sent, signed, certificates, files the
  client sent back, files you upload);
- a Home dashboard (counts you can select to open each list), reports on screen and in Excel, and an activity log of
  who generated, opened, viewed, signed or downloaded what, when and from where;
- user accounts and permissions;
- encrypted backups with a nightly restore test;
- a virus scan of every file that comes in: uploads, signer attachments and files clients attach to email replies;
- HTTPS certificates obtained and renewed automatically (Caddy with Let's Encrypt).

The Sales CRM, company single sign-on and file storage are not part of this package. People sign in with their email
address and a password that the Super Admin creates for them.

## What you need

| | |
|---|---|
| Server | Ubuntu 24.04 or 22.04 LTS, x86_64. **Recommended: 4 vCPU, 16 GB RAM, 200 GB SSD** (what release 5.1 is sized for; see "Server size and capacity"). Minimum 2 vCPU, 4 GB RAM, 40 GB SSD: the installer then starts fewer processes. Measured in testing: about 1.6 GB of memory in use after a complete signing run, 0.95 GB of it the virus scanner |
| Access | SSH as root (or a user with `sudo`) |
| Address | One web address for Agreements, for example `agreements.yourcompany.com`, on a domain you control |
| Mailboxes | The compliance mailboxes (Google Workspace): compliance@scribealign.com, compliance@extendedhealthservices.us, compliance@24-7consultancyllc.com, each with an app password (step 5) |
| Safe place | A password manager for the generated secrets |
| Later | A clear image of each company signatory's signature (step 5), or draw it on screen; optionally an S3 bucket elsewhere for off-site backup copies |

## 1. Point the address at the server

At your DNS provider, create one **A record**, for example `agreements.yourcompany.com`, with the server's public IP
address. If your provider has a firewall in its control panel, allow incoming TCP **22, 80 and 443**, and outgoing
connections to `smtp.gmail.com:587` and `imap.gmail.com:993` (some providers block outgoing mail ports on new
accounts until you ask them to open them). The installer opens 80 and 443 in `ufw` when it is active; everything else
stays closed.

## 2. Copy this ZIP to the server and unpack it

```
scp TFSC-Agreements-Deploy-*.zip root@YOUR_SERVER_IP:/opt/
ssh root@YOUR_SERVER_IP
apt-get update && apt-get install -y unzip
cd /opt && unzip TFSC-Agreements-Deploy-*.zip && mv TFSC-Agreements-Deploy-*/ tfsc-agreements && cd tfsc-agreements
```

## 3. Install

```
sudo ./agreements.sh install
```

It asks for the web address and the time zone of the nightly backup. Then it installs Docker if it is missing (it
asks first), generates every password and key, builds the application images (2 minutes in testing on 4 vCPU; allow
up to 10 minutes on 2 vCPU), starts everything, checks the HTTPS address and prints a one-time **setup key**.

**Right away:** copy `/opt/tfsc-agreements/deploy/agreements/.env` into your password manager. It holds
`BACKUP_PASSPHRASE`, the only key to the encrypted backups.

If the address shows `NOT YET`, DNS does not point at the server yet or port 80/443 is blocked; Caddy keeps trying.
Check again with `./agreements.sh status`.

Unattended install (no questions): `TFSC_DOMAIN=agreements.yourcompany.com sudo -E ./agreements.sh install --yes`

## 4. Create the Super Admin and your team

1. Open `https://agreements.yourcompany.com/login`. Enter the **setup key** with your name, email and a password
   (at least 12 characters). You are now the Super Admin. The key works once; `./agreements.sh setup-key` shows it
   again until it is used.
2. **Admin > Users & permissions** > **Create user**: name, email, role (User, Admin or Super Admin) and the companies the
   person may prepare agreements for. The screen shows a temporary password; give it to the person privately. They
   choose their own password at first sign-in. Use **Reset password** there if someone forgets theirs.

## 5. Company settings: who signs, and the signature on every agreement

**Admin > Company settings** (Super Admin only) has one card per company:
- **Authorized signatory** and **Title**, as they print under the company's signature line. Set out of the box:
  Naeem Abbas, Chief Executive Officer, for Scribe Align and Extended Health Services; Danish Ather, Chief Operating
  Officer, for 24-7 Consultancy. Change them here if that is not right.
- **Signature on agreements**: **Upload** a photo or scan of the signature (dark ink on white paper; the paper is made
  transparent and the image trimmed automatically), **Draw** it with a mouse, pen or finger, or **Type** the name and
  choose one of 16 handwriting styles. The preview shows it on the signature line as it will print. **Save**.
- **Size on agreements**: how large the signature prints, from 30 % to 100 % of the largest size (2.3 × 0.96 inch);
  the preview follows the slider. Set out of the box: 60 % for Naeem Abbas (Scribe Align and Extended Health
  Services) and 85 % for Danish Ather (24-7 Consultancy), as approved; **Default** returns to these.

From then on every generated BAA, Services Agreement and Mutual NDA for that company carries the signature on the
line, with the signatory's name, title and the date. The builder shows the company's signature before you generate,
and warns when a company has none. The images are kept in the database, so the backups include them.

Signature images installed earlier with `./agreements.sh signatures` keep working; saving one in Company settings
replaces them.

**Edit company details** (on every company card, including Scribe Align, Extended Health Services and 24-7
Consultancy) changes the short name, display name, notice email, **address**, governing law, time zone, brand colours,
logo, letterhead (.docx) and page margins. Every agreement generated afterwards prints the company's address and notice
email from here; agreements already generated or signed keep the details they were issued with. The legal name is
fixed, because it identifies the company on every agreement, envelope, mailbox and user permission.

**Five more companies** come with this release, each with its letterhead, logo, address and governing law: House of RCM,
LLC (Austin, Texas law), Quantum Medical Services (Spokane, Washington law), MedEquity Solutions, Inc. (Rexburg, Idaho
law), 3E Medical Billing, LLC (Round Lake, Illinois law) and Billing Nest, LLC (Laurel, Maryland law). They offer the
standard service catalogue. **Before the first agreement for each of them**, open **Edit company details** on its card
and enter the **Authorized signatory** and **Title** (and a signature under **Signature on agreements**); until then the
company's tile says "Signatory not set" and generating for it explains what to add. A company you had already added
by hand under the same legal name is left exactly as it is.

**Letterhead check.** When you upload a letterhead (adding a company or changing one), the app lays out a page on it the
way the server prints agreements and measures where the header artwork ends and the footer artwork starts. It then sets
the margins so the agreement text and the "Page X of Y" line never sit on the logo or the footer, and shows what it
measured (header, footer, page size, margins) under the upload. You can still adjust the margins under **Advanced**.

An added company with no services of its own offers the standard service catalogue; add services to its card to offer
only those instead.

The same screen holds the workspace settings shown on every page and email: portal name, copyright holder (the
"© year ... All rights reserved." line), the **Help email** (muzahir@scribealign.com) and the security line on the
signing page.

## 6. Connect the company compliance mailboxes

Agreements sends signing requests, reminders and completed documents from each company's compliance mailbox, and reads
that inbox (read-only) for client replies and bounce notices, which appear on the envelope. All three domains use
Google Workspace. Do this once for each of compliance@scribealign.com, compliance@extendedhealthservices.us and
compliance@24-7consultancyllc.com:

1. Sign in to Google as that mailbox. Under Google Account > Security, turn on **2-Step Verification**.
2. Open https://myaccount.google.com/apppasswords, create an app password named "TFSC Agreements" and copy the
   16 characters.
3. In Agreements: **Admin > Company mailboxes** > the company > "Send from this company's own mailbox", sign-in method
   **App password**, paste it, keep "Read replies and bounce notices" on, and **Save**. The Google Workspace server
   settings are filled in.
4. Select **Send test email**, then **Check inbox**. Both must report success; the message shows the real reason if not.
5. Set the **sender name** to the company name (for example "Scribe Align LLC"); it is what the inbox shows.
6. If the company signs electronically (instead of the saved signature image), enter the signer's email
   (Naeem Abbas or Danish Ather) in "Company signer's email".

### The verified tick and the company logo in Gmail

**Admin > Email trust & verified sender** checks each company domain's SPF, DKIM, DMARC and BIMI records live and
gives the exact record to publish for anything missing (**Copy record**). The blue verified tick in Gmail is granted
by Gmail, not by any sending application (DocuSign gets it the same way), and needs all of:
1. SPF and DKIM passing for the company domain (Google Workspace signs with DKIM once it is turned on in the Google
   Admin console: Apps > Google Workspace > Gmail > Authenticate email);
2. DMARC at enforcement: `p=quarantine` or `p=reject`, applied to all mail (no `pct` below 100);
3. the logo as an **SVG Tiny PS** file (square, solid background), uploaded on the same screen under
   **Gmail logo and verified tick**: the application checks the format and serves it at `https://<your address>/bimi/...svg`;
4. a **Verified Mark Certificate (VMC)** for that logo from DigiCert, Sectigo or GlobalSign. A VMC needs the logo to be a
   registered trademark. Upload the certificate (.pem) on the same screen; the application checks it and serves it.
   A Common Mark Certificate (no trademark needed) shows the logo in Gmail but **not** the blue tick;
5. the BIMI record the screen shows (`default._bimi.<domain>`, `v=BIMI1; l=...; a=...`), published at your DNS provider.

The **Gmail verified tick: still to do** box on that screen lists exactly what is still missing for each company. Gmail can take
a few days to show the tick after everything passes. Until then, set each compliance mailbox's Google profile photo to
the company logo: Gmail shows it to many recipients right away.

If your Google Workspace administrator has turned off app passwords, use **Google Workspace service account** instead:
create a service account and a JSON key in Google Cloud, then in the Google Admin console (Security > Access and data
control > API controls > Domain-wide delegation) add its client ID with the scope `https://mail.google.com/`, and
upload the JSON key on the Company mailboxes screen.

## 7. Clients

**Clients** is the register of every client you can see. Every client has a permanent **client number** (CL-0001,
CL-0002, ...): clients already in the app when you update are numbered in the order they were created, every new client
gets the next number, and a number never changes and is never given to anyone else, even after a client is deleted.
The database itself enforces this (not only the screens): it refuses to change a client's number or to give a used
number to another client, and keeps a permanent record of every number ever given. Search by client number
("CL-0012", "cl-12" or "12"), name, NPI, email or address; filter by company, by where agreements were signed (this
portal, DocuSign, manual email, Jotform) and by real or test clients.

The register has one column per agreement: **BAA**, **Services Agreement** and **NDA**, each showing Signed (with the
date), Out for signature, or Not signed. The buttons above it show **All**, **BAA + Services signed**, **Waiting**
(out for signature), **No BAA** and **No Services**, with a count on each.

**Several clients at once.** Tick clients (or the box in the header for the whole page) and a bar appears with
**Mark as test**, **Mark as real**, **Export (.xlsx)**, **Export (.csv)** and, for a Super Admin, **Delete**.

**Deleting a client (Super Admin only)**, from the bar above or from a client's record (**More actions > Delete
client...**): a confirmation names every client and how many there are ("Delete 3 client records?") and says what
happens; nothing is deleted until you confirm. A deleted client:
- disappears from Clients, searches, Create agreement, the address book and the client reports, and goes to
  **Clients > Trash** for 30 days;
- can be restored from the Trash during those 30 days with everything as it was: the same client number, its
  agreements, signed copies, contacts, files and history;
- after 30 days is deleted permanently: its details, contacts and uploaded files are removed and its client number is
  retired (never given to anyone else). **Signed agreements and their Certificates of Completion are kept** as legal
  records (HIPAA asks for six years): the completed envelopes stay under **Agreements** with their signed copies;
- does not affect anything already sent: an envelope out for signature when its client is deleted can still be signed
  by the client and completes normally.
Every delete, restore and permanent deletion is written to the activity log with who did it and when.

**Agreements signed before this portal** (returned by email, signed in DocuSign or submitted through Jotform):
- On a client's record, **Agreements** > **Add a past agreement**: choose the signed PDF. The app reads what the file
  contains (DocuSign and Jotform PDFs usually carry the envelope or submission ID, the signer, the dates and the company)
  and fills in the form; check it, correct anything, and **Save to client record**. The original file is kept unchanged.
- Many at once: **Clients** > **Import previous clients**. Upload an optional spreadsheet (download the template from
  that page; .xlsx or .csv, one row per client) and any number of signed PDFs. The app matches each file to a client (by
  client number, DocuSign envelope ID, the name in the file or the file name), shows what it will create or add, and
  nothing is saved until you press **Import**. New clients get the next client numbers.

**Signed agreements register** (from the Clients page): choose **BAA**, **Services Agreement** or **Mutual NDA**, then
the company: every client's signed agreement of that kind, wherever it was signed, the coverage per company and the
clients still missing one. **Export register** (.xlsx or .csv) for an audit, or download all the signed copies as one
.zip. A client's record shows **Signed agreements by company** (BAA and Services Agreement for each company).

A client's profile has:
- **Overview**: address, phone, email, website, specialty, NPI, tax ID and notes (**Edit details**).
- **Contacts**: the people at the client (signatory, billing, compliance). Everyone you send an envelope to for the
  client is added automatically; contacts are suggested when you type a recipient's name or email.
- **Documents**: everything kept for the client in one list: generated agreements (Word and PDF), documents out for
  signature, signed documents, Certificates of Completion, files a signer attached while signing, files the client
  attached to an email reply, and files you upload (PDF, Word, Excel, CSV, text, PNG, JPEG; up to 15 MB; virus-scanned).
  Any uploaded document can be sent for signature from here.
- **Signatures**, **Agreements** and **Activity**: every envelope, every generated agreement and the full history.

Use **New client** to add a client before preparing anything, **Create agreement** to open the client in the
agreement builder, and **Send documents for signature** to start an envelope for the client with the client's
agreements and files offered first. The client's **Activity** tab shows who generated, sent, opened, viewed, signed
and downloaded each document, and when.

## 8. Send documents for signature

1. **Home > Start > Send an envelope** (or **Agreements > Start**, or **Send documents for signature** on a client).
   Choose the company on the company page (one tile per company, including companies added later under Admin >
   Companies), and the client if there is one. **Documents**: drag files from your computer (PDF, Word: DOCX,
   DOC, ODT, RTF; text, PNG or JPEG; everything is converted to PDF), or pick the client's generated agreements and
   files. Or create the agreements first: **Create agreement** (company, client, BAA, Services Agreement and/or Mutual
   NDA, services and terms; the governing state starts at the company's state), **Generate**, then **Send for
   signature**.
2. **Recipients**: one card per person with name, email and a role (**Needs to sign**, **Signs in person**, **Needs to
   view**, **Receives a copy**). The client's signatory is filled in. **Customize** adds an access code or a private
   message for that person; the bin removes them; **Add recipient** adds another. Turn on **Set signing order** to
   number the people (the same number means at the same time); without it everyone receives the email at once.
3. **Fields** are placed for you: the signature, name, title and today's date on the signature block, required fields,
   and initials on every page in a free spot near the bottom. Each field is a soft tag in its signer's colour (the client
   in violet, a second signer in green, then blue, rose...) with an icon for its kind and a red star when it is
   required; the selected field turns solid. Drag any field to move it, select it to change or delete it, or drag new
   ones from the palette. **Auto-place fields** places them again; **Initials on every page** adds initials for the
   selected person. When the agreement was generated with the company's saved signature (the normal case), only the
   client has fields: the company's signature, name, title and date are already printed on the agreement.
4. **Email subject**: written for you from the documents, for example "Please Sign: Business Associate Agreement and
   Services Agreement - Client name" (one agreement: only that one), marked **Automatic** and updated whenever you add
   or remove a document. Type your own and it is marked **Yours** and sent exactly as you typed it, in the invitation,
   the reminders and the completed copy; **Use the automatic subject again** goes back.
   **Options**: the documents **never expire** unless you choose an expiry; reminders; attaching the completed
   documents to the final email; whether recipients may assign the documents to someone else. **Send**.
5. The client receives an email in the company's colours from the company's compliance mailbox, sent in the company's
   registered name ("Extended Health Services Inc. sent you documents to review and sign"), never the name of the
   person who prepared it; the signing page, the reminders and the Certificate of Completion name the company the same
   way. The email has a **REVIEW DOCUMENTS** button (**REVIEW DOCUMENT** when there is one). On the signing page they accept the electronic record disclosure, then **Start** takes them to the
   initials on page 1, then page 2, 3 and so on, then the signature, then the title (today's date is already filled in).
   With two agreements in one envelope the title is copied into the second ("Copied from your ... · Edit") while the
   signature is still asked for on each agreement. Signatures carry a "Signed by:" bracket with an ID that is the same
   on the screen, on the signed PDF and on the Certificate of Completion. They adopt a signature and initials: one of 16 styles made from their
   name (initials can use a different style), drawn, or uploaded (signature and initials separately). **Finish**.
   Under **Other actions** they can **Finish later**, **Assign to someone else**, download, print or decline.
6. **Completion**: the envelope completes when everyone who needs to sign has signed. Someone who only needs to view
   never holds it up (unless a signing order puts them before a signer). Everyone, including viewers and copy
   recipients, then receives the completed documents and the Certificate of Completion by email, and the compliance
   mailbox gets a copy.
7. Follow progress under **Agreements** (Action required, Waiting for others, Expiring soon, Completed, Drafts, All
   sent, Declined, Voided, Expired): who has viewed and signed, email delivery, client replies, the history.
   **Resend**, **Correct**, and under **More**: copy into a new envelope, save as template, void. Signing links never
   expire on their own; after completion the links in recipients' emails keep opening the completed documents until you
   choose **More > Turn off recipient links**.
8. **Bulk send** (Home > Start) sends a template to a list of people, each in their own envelope.
9. **Test agreements** (practice runs): **Create agreement** opens on the company page; the company you pick replaces
   the dropdown, with **Change company** beside it (it asks first). Turn on **Test agreement** in the summary card to
   make a practice run: its documents are watermarked TEST, its emails start with "[TEST]", signers see a TEST banner,
   and it never counts as a real client or agreement: Home, Insights, reports, the Agreement log and client counts leave
   it out. **Agreements > Test agreements** lists them; the **Real clients / Test / All** filter and **Mark as test** /
   **Mark as real** work on any list. Reports can include test work when you tick **Include test agreements**.
10. Anyone can check a completed PDF at `https://agreements.yourcompany.com/verify`.

## 9. Home, reports and the activity log

- **Home**: Action required, Waiting for others, Expiring soon and Completed; select a count to open that list. Below:
  documents sent, pending signature, completed, completion rate and agreements generated (for all companies or one),
  the latest activity and quick actions.
- **Reports > Insights**: sent and completed by month, completion funnel, time to complete, by company, top clients,
  each chart with its table.
- **Reports > Report builder**: choose the data (envelopes, recipients, generated agreements or clients; for Admins
  also user activity and users), the columns, the dates, the company, the status, the client and the person; **Run
  report** to see it on screen, **Download Excel** or **Download CSV**, and **Save this report** to run it again.
- **Activity log** (Admins and the Super Admin): every action by people, signers and the system, with time, person,
  action, client, IP address and browser; filter by when, who, what, client or a search, and download it in Excel or
  CSV.
A User sees their own work, an Admin their own and their team's, and the Super Admin everything.

## 10. Backups

- Automatic: every night at 2:00 in your time zone (full backup weekly, changes daily, 14 days kept), every database
  change within a minute in between, all encrypted. After each backup a restore test rebuilds a copy and checks it.
  See the results with `./agreements.sh status`.
- Take one now (for example before an update): `sudo ./agreements.sh backup`.
- **Off-site copy (strongly recommended):** the backups above are on the same server. Create a bucket at another
  provider (any S3-compatible storage), copy `deploy/agreements/backup-offsite.env.example` to `backup-offsite.env`
  in the same folder, fill it in, and run `sudo ./agreements.sh start`.
- Restoring after a disaster (the database is lost or damaged), from `/opt/tfsc-agreements/deploy/agreements`:
  ```
  docker compose --env-file .env stop agreements agreements-worker backup postgres
  docker compose --env-file .env rm -f backup postgres
  docker compose --env-file .env run --rm --no-deps --user postgres --entrypoint tfsc-backup postgres restore
  docker compose --env-file .env up -d
  ```
  Add a time to go back to a moment before a mistake, for example `... postgres restore "2026-10-21 09:30:00-07"`.
  On a new server: install this package with the saved `.env` (it holds `BACKUP_PASSPHRASE`) and the backup volume or
  the off-site bucket, then run the commands above.

## Everyday commands

| Command | What it does |
|---|---|
| `./agreements.sh status` | what is running and whether it is healthy, the HTTPS address, the latest backups, free disk space |
| `sudo ./agreements.sh rollback` | go back to the version that ran before the last update (data is kept) |
| `./agreements.sh logs` or `./agreements.sh logs agreements-worker` | follow the logs (Ctrl+C to stop) |
| `sudo ./agreements.sh backup` | full backup now, then the restore test |
| `sudo ./agreements.sh restart` / `stop` / `start` | restart, stop or start everything |
| `./agreements.sh help` | every command |

## Updating to a new release

```
cd /opt && unzip TFSC-Agreements-Deploy-NEW.zip
cp tfsc-agreements/deploy/agreements/.env TFSC-Agreements-Deploy-NEW/deploy/agreements/   # and backup-offsite.env if you use it
mv tfsc-agreements tfsc-agreements-$(date +%Y%m%d-%H%M) && mv TFSC-Agreements-Deploy-NEW tfsc-agreements && cd tfsc-agreements
sudo ./agreements.sh backup && sudo ./agreements.sh update
```

The old folder is kept under a dated name (for example `tfsc-agreements-20261106-1430`), so the settings it holds are
never lost and a folder from an earlier update is never overwritten. Data lives in Docker volumes and is kept: users, clients, agreements, envelopes, signatures, mailboxes and backups.
Database changes are applied automatically during the update; they only ever add tables and columns.

What `update` does, in order, so nothing is lost and the site is never left down:
1. checks there is room on the disk, then saves a copy of the database in the backups volume while the current
   version keeps serving. From release 5.1 on the copy is encrypted with your backup passphrase
   (`pre-update-<date>.dump.enc`, the 2 most recent are kept; copies left by earlier releases are encrypted too).
   If the copy cannot be saved, it stops and changes nothing;
2. builds the new version while the current one keeps serving (about 6 minutes when its libraries change);
3. applies the database changes while the current version still serves. Changes only ever add; if one fails,
   PostgreSQL undoes it completely, nothing is switched and the current version simply keeps running;
4. restarts on the new version (a few seconds; a signer who clicks during those seconds is reconnected automatically
   and loses nothing);
5. if the new version is not healthy within 4 minutes (or reports an error while starting, which is noticed after
   about a minute), it puts the previous version back on the same data and tells you. The database is left as it is:
   the previous version runs on it, and the next update does not repeat what was already done.

A dropped SSH connection no longer stops an update half way. If a new version starts but you want the previous one
back, run `sudo ./agreements.sh rollback`.

Agreements already sent for signature keep working through the update: the links in those emails, the answers a signer
saved to finish later, and the signed copies already issued stay exactly as they were.

To restore the pre-update copy by hand (only if support asks you to):
```
cd /opt/tfsc-agreements/deploy/agreements && docker compose --env-file .env stop agreements agreements-worker
docker compose --env-file .env exec -u postgres postgres ls /backups          # pick the pre-update-....dump.enc
docker compose --env-file .env exec -u postgres postgres sh -c 'openssl enc -d -aes-256-cbc -pbkdf2 -pass env:PGBACKREST_REPO1_CIPHER_PASS -in /backups/pre-update-YYYYMMDDTHHMMSSZ.dump.enc | pg_restore -U crm -d tfsc --clean --if-exists'
# a copy from before release 5.1 that still ends in .dump (not encrypted):
#   docker compose --env-file .env exec -u postgres postgres pg_restore -U crm -d tfsc --clean --if-exists /backups/pre-update-YYYYMMDDTHHMMSSZ.dump
docker compose --env-file .env start agreements agreements-worker
```

### This update (November 10, 2026: release 5.1, the audit fixes) also

Nothing you have is changed: every client, client number, generated agreement, envelope, signing link, signature, signed
copy, certificate, BAA and mailbox setting stays exactly as it is. The database change (0012) only adds columns and
indexes. People who are signed in stay signed in.

**What you will notice**
- **System status on Home (Super Admin):** one strip that says whether email, signed copies, document generation, the
  virus scanner, backups and disk space are working, with a button to the screen where a problem is fixed. The same check
  is at `/api/health/deep` for an outside uptime monitor (it answers 503 when something is broken).
- **Automatic sign-out:** staff are signed out after 30 minutes without activity (a warning counts down two minutes
  before, with "Stay signed in"); signers after 15 minutes. After signing in again you return to the same screen, and an
  agreement you were preparing is put back. Change the times in `.env` (`TFSC_IDLE_MINUTES`, `TFSC_SIGNER_IDLE_MINUTES`).
- **Sign-in limits count wrong passwords only**, so a whole office behind one internet address can sign in at 9 am.
- **Email is never dropped because a mailbox had a bad hour.** When Google's daily sending limit is reached, the app
  password is wrong or the mail server cannot be reached, sending for that company pauses and its queue is kept; other
  companies are not delayed. The mailbox card and the status strip say why and until when. Saving the mailbox settings
  or a successful "Send test email" resumes at once. Each mailbox stops by itself at 1,800 emails a day
  (`TFSC_MAIL_DAILY_CAP`; Google allows 2,000).
- **An envelope shows "Not delivered yet, retrying" with the reason** from the first failed try, not 14 hours later.
- **One odd reply can no longer block a company inbox:** a message that cannot be processed is set aside and reading
  continues; replies with attachments wait (are not dropped) while the virus scanner is away.
- **Bulk send:** commas in names are kept ("Hill Country Allergy & Asthma, PLLC"; "Rebecca Moore, MD"). The email
  address anchors each line; a private message comes after a `|`. Each row shows "Existing client CL-0007" or "New client
  will be created", and offers the existing record when the name is nearly the same. `{{first_name}}` skips titles.
- **Correcting a sent envelope:** if someone else changed it while you had it open, you are asked to reload instead of
  silently undoing their fix. Every correction is in the Activity log.
- **Save as template** keeps each recipient's role and fields, not the person, unless you tick "Keep".
- **Disabling an Admin** asks who takes over their team; nobody is locked out.
- **Imports:** importing the same spreadsheet twice, a repeated row or a double click adds nothing twice; "Billing" alone
  is no longer taken for 3E Medical Billing; a signed date in the future is refused.
- **Names and addresses are printed as typed** (long dashes are no longer rewritten). Whole numbers are required for days
  and years, and dates must be between 2000 and 2100.
- **A user limited to certain services** cannot get around it with a custom service or the preview, unless you allow
  custom services for that user (Users & permissions).
- **Trash:** when a client is purged after 30 days, their unsigned generated agreements are removed too; signed
  agreements and certificates are kept as before.
- **Uploads:** a PDF with only an owner password is accepted; PDFs with a crop box keep fields where people see them.

**Screens (phones, tablets, touch)**
- Agreements on a phone or tablet: the folders are one row of chips above the list, so the envelopes are on the first
  screen. Users & permissions on a phone shows each person as a card with their buttons, nothing hidden off the edge.
- Sign-in on a phone shows the form first. Buttons and checkboxes are larger on touch screens. Email addresses no longer
  break in the middle. Grey text has more contrast. Motion is reduced for people who ask their device for that.
- Signing page: the title no longer runs under the Finish button on small phones; "Other actions" is announced to screen
  readers; the Decline box has room to write.
- A workspace that cannot load says so after 15 seconds and offers "Try again"; a wrong address shows a proper "page not
  found"; opening a screen you have no access to says so.

**Speed and capacity** (see "Server size and capacity" below)
- The freeze at about 1,000 people (every connection waiting for another) is fixed; a busy moment gets a short "busy,
  try again" instead of a 30-second wait.
- Web: one process per processor core. Background work: separate processes for document generation (2), signed copies
  (2), email and reminders, so a burst of signed envelopes or a slow mail server delays nothing else.
- A new agreement's PDF is analysed once and the result is shared by all processes (creating an envelope from it no
  longer repeats seconds of work); Insights are remembered for a minute; turning a report page does not rebuild the report.
- Scripts and style sheets are cached by the browser; twelve database indexes; the Clients and Signed agreements
  registers read each table once; page images are drawn several at a time and tidied up after 30 days.
- Logs rotate (they can no longer fill the disk); leftovers from crashes are removed hourly.

**Security**
- The signing history can no longer be changed in the database (the database itself refuses it), and every entry carries
  a seal made with your installation's secret key.
- An image that claims to be enormous is refused before it is opened; the PDF library is updated (7 published flaws);
  the storage-folder chooser no longer lists other companies' client folders; a wrong email and a wrong password take the
  same time to refuse; a mailbox cannot be pointed at this server's private network.
- Every Python package is pinned (`apps/agreements/requirements.lock`).

**After the update, please do these three things**
1. Open Home as the Super Admin and read the System status strip. "Off-site backup: needs a look" is expected until
   you set up the off-site copy (step 10); it is the one way to survive the loss of this server's disk.
2. Admin > Company mailboxes: each connected mailbox should say Connected.
3. Send yourself a test envelope and sign it.

Updating to version 5 also:
- removes the 30-day expiry that earlier releases gave every envelope by default (an expiry someone chose is kept);
- prints company signatures at the approved sizes (Naeem Abbas 60 %, Danish Ather 85 %; adjustable per company);
- completes envelopes that were waiting only for someone who needed to view them;
- keeps signature images installed with `./agreements.sh signatures` until you save one under Admin > Company settings.

This update (November 8, 2026) also:
- lays out every agreement the same way on every letterhead: one footer line in one font and size on every page
  (the agreement's name and reference in the middle, "Page X of Y" on the right), and the letterhead's own artwork kept
  exactly where its designer put it; agreements generated before the update are not changed;
- replaces Billing Nest's letterhead with the corrected one (with its full Laurel, Maryland address); if you had
  already uploaded a different Billing Nest letterhead yourself, yours is kept;
- sends every email, signing page and certificate in the company's registered name (above);
- writes the email subject from all the documents in the envelope and keeps a subject you typed (above); envelopes
  already sent keep the subject they were sent with;
- adds the agreement columns and filters, the bulk actions, Delete with the 30-day Trash, the Signed agreements
  register and the database guard on client numbers (step 7); every client number already given is recorded as
  permanent, and no client is deleted or changed by the update.
- restores the look of the public Verify page (`/verify`), which lost its styling when the signing page was redesigned
  (checking a file always worked); it now shows a clear green "Verified" or red "Not verified" result;
- greets signers and fills the First name and Last name fields without titles or credentials ("Dr. Rebecca Moore, MD"
  is Rebecca Moore); documents already signed are not changed.

The November 6, 2026 update also:
- gives every client already in the app a client number (CL-0001 onwards, oldest first); nothing else about the client
  changes;
- adds the five companies above (House of RCM, Quantum Medical Services, MedEquity Solutions, 3E Medical Billing and
  Billing Nest); set each one's signatory in Company settings before its first agreement;
- leaves every agreement already generated, every envelope already sent and every signed copy exactly as it was.

## Server size and capacity

The installer (and the first update to release 5.1) looks at the server and writes its sizing into `.env` once
(`WEB_WORKERS`, `TFSC_GENERATION_WORKERS`, `TFSC_COMPLETION_WORKERS`, `PG_SHARED_BUFFERS` ...). On **4 vCPU / 16 GB /
200 GB** that is: 4 web processes with 16 request threads each, 2 generation and 2 signed-copy workers, one process each
for email and for reminders/inbox reading, and PostgreSQL with a 3 GB cache and 200 connections. Typical memory in use:
about 5-6 GB, most of the rest serves as the database's file cache. After resizing the server, delete those lines from
`.env` and run `sudo ./agreements.sh update`.

What this server can be expected to carry (from the November 2026 load tests and the measurements with this release):
- **People working at the same time:** about 1,500 to 2,000 with pages under a second. The limit is the four
  processors. 5,000 at the same time needs about 18-20 processor cores (a larger server, or two application servers);
  nothing in the application has to change for that, the processes are added in `.env`.
- **Envelopes:** 1,000 and more a day. Sending about 160 a minute, signed copies about 110 a minute with two workers.
- **Email:** Google Workspace allows 2,000 messages per mailbox per day, about 220 two-signer envelopes per company per
  day. Above that, sending pauses until the next day (nothing is lost); a company that sends more needs Google's SMTP
  relay or a sending service.
- **Disk:** every PDF is still kept in the database, about 3.4 MB per completed envelope. 200 GB is room for roughly
  40,000 completed envelopes including backups. Watch "Disk" in `./agreements.sh status`; the status strip warns below 10 %.

## Changing settings later

- Settings are in `deploy/agreements/.env`: edit, then `sudo ./agreements.sh start`.
- **Never change** `POSTGRES_PASSWORD` or `BACKUP_PASSPHRASE` after the install. If `TFSC_SECRET_KEY` changes, enter
  the mailbox passwords again under Company mailboxes.
- A different web address: change `TFSC_DOMAIN`, point DNS at the server, `sudo ./agreements.sh start`. Signing links
  already emailed use the old address.

## When something is wrong

| Symptom | Check |
|---|---|
| The address shows `NOT YET` or the browser warns about the certificate | DNS points at this server (`getent hosts agreements.yourcompany.com`), ports 80/443 open at the provider; `./agreements.sh logs edge` |
| A company mailbox shows a problem, or signing emails stay "Retrying" | Company mailboxes > Send test email shows the reason (wrong app password, 2-Step Verification off, outgoing port 587 blocked by the provider) |
| Someone cannot sign in | Users & permissions > Reset password (or Enable, if the account was disabled) |
| Pages are slow or services restart | Home > System status; memory: `docker stats --no-stream`; sizing: "Server size and capacity" |
| "The system is busy right now" | a short overload: try again. If it stays, see System status and `./agreements.sh logs agreements` |
| An update went back to the previous version | send `deploy/agreements/last-failed-update.log` to support; your data is unchanged |
| Anything else | `./agreements.sh status`, then `./agreements.sh logs <service>` |

## What was tested, and what was not

### Release 5.1 (November 10, 2026)

- **Automatic tests:** 287 tests (255 from before, 32 new for the audit fixes) pass on SQLite and, for the first time for
  the whole suite, on PostgreSQL 16, the database your server uses.
- **Update rehearsal on PostgreSQL 16 with real work in progress:** the released version (November 8) was started on an
  empty database; a Super Admin, a user and a mailbox were set up; two BAA + Services Agreement packages were generated
  with LibreOffice; one envelope was signed and completed; on a second one the signer saved 8 answers to finish later; a
  draft was left open. Then this release was started on the same database. Result: the change was applied in 2 seconds;
  the person signed in before the update was still signed in; clients and client numbers, both envelopes with their
  history, the signed PDFs, the certificate and the generated Word and PDF files were byte for byte identical (compared
  by SHA-256); the signing link emailed before the update opened and showed the 8 saved answers; the signer finished and
  the new worker completed the envelope; the audit trail verified and was sealed; a new agreement was generated, sent and
  emailed; a stale correction was refused; the old draft could be deleted.
- **Step-back:** the previous version was then started again on the updated database: sign-in worked, the same signed
  copies and certificates were served, and it generated, sent and emailed a new envelope. Updating again afterwards
  worked (nothing was repeated).
- **Load check (4 processor cores shared with the test client):** 400 clients requesting Home, the lists, the Clients
  register and the configuration without pause for 20 seconds: 4,768 requests, no errors, no connection failures.
- **Not tested here:** building the Docker images and running `agreements.sh update` end to end in Docker (this test
  environment cannot reach the Debian and Alpine package servers the image builds need; the compose file was validated
  with `docker compose config`, and both scripts with a syntax check); email through real Google Workspace mailboxes;
  the off-site backup copy; a 5,000-user load test. Run `sudo ./agreements.sh backup` before the update as the guide
  says; the update itself also saves a copy and steps back by itself if the new version does not start.

Every test below unpacked this ZIP and ran `./agreements.sh` as described above, on Docker 29 (Linux, 4 vCPU, 16 GB).
Two things differed from your server: Caddy issued its own test certificate (a test machine has no public address for
Let's Encrypt), and a test mail server (GreenMail) played the part of the Google Workspace mailboxes.

- **Install:** unattended, from an empty build cache (base images already downloaded): 2 minutes. Later
  installs with the images built: 15 to 35 seconds. `.env` is readable by root only; only ports 80 and 443 are open to
  the outside.
- **Accounts:** the Super Admin was created with the setup key (a wrong key was refused; the key works once). A user
  limited to Extended Health Services had to replace the temporary password, could not list or create accounts, could
  not change a mailbox, saw only that company's mailbox status and could not prepare agreements for Scribe Align.
- **Agreements:** BAA, Services Agreement and Mutual NDA generated together, all three governed by Missouri law by
  default for Extended Health Services; a chosen state (Nevada) replaced it; the company's signature block is dated with
  the effective date. A signature image installed with `./agreements.sh signatures` appeared on a 24-7 Consultancy
  agreement (Georgia law), and not on agreements generated without it.
- **Signatures:** mailbox saved (the password is encrypted and never shown again), Send test email and Check inbox
  succeeded. On an envelope with the client's fields placed automatically on all three agreements, initials and a CC
  recipient were added, then it was sent from the compliance mailbox. The sender was emailed when the client viewed and
  signed; the client signed, then the company signer; the worker completed it. The history verified, the Certificate
  of Completion and `/verify` worked, and the client, the CC and the compliance mailbox each received the three signed
  agreements with the certificate. Two client email replies were read from the inbox and shown on the envelope: the
  attachment was virus-scanned and kept, and the standard EICAR test virus was blocked. In a browser: sign-in, every
  screen, and a client signing on a phone-sized screen.
- **DocuSign-style features, in a browser over HTTPS** on a server updated from the previous release: a client created
  with its details, a contact added, a PDF uploaded to the profile and sent for signature from there; the recipient
  picked from the address book; signature, phone-validated text, checkbox with a conditional field, radio group,
  attachment and note placed; the email arrived from the compliance mailbox with the company logo; the client accepted
  the disclosure, a wrong phone number was flagged, the conditional field appeared when the box was checked, Finish
  later kept the entries for the same link, a photo was attached, the signature adopted and Finish completed it; the
  signed document, the certificate and the attachment appeared in the client's documents; Reports and Bulk send opened;
  a phone-sized screen showed the completed page. A Word document was converted in the container (2.4 seconds), a bulk
  send of three reached all three people, and the EICAR test virus uploaded to a client profile was blocked.
- **Backups:** `./agreements.sh backup` made a full encrypted backup in 12 seconds, then the restore test rebuilt the
  database at a point in time and checked it; `status` lists both.
- **Update:** from the previous release of this package, holding signed envelopes, following "Updating to a new
  release": the database changes were applied, and accounts, clients, envelopes, their histories and the signed PDFs
  were identical afterwards (the update took 30 seconds with the images built).

**This update (November 8, 2026), tested from this ZIP on the same Docker host**, over the release you have deployed
(the November 6 ZIP, e3f9c3a) holding 11 clients (CL-0001 to CL-0011), 8 completed envelopes and 3 envelopes left
mid-way: one sent and not opened, one signed by the client and waiting for the company, one opened by the client
(consent given, signature adopted) and left. The steps in "Updating to a new release" were followed exactly:
- `backup` and `update` finished in 32 seconds; the database went from 0009 to 0011 and the pre-update copy was saved.
- All 39 tables were identical row for row afterwards; one new table holds the 11 client numbers as permanent.
- The three envelopes left mid-way kept the subjects they were sent with, were finished on the new version from the
  links emailed before the update (the opened one remembered the consent) and completed; the signed copies and
  Certificates of Completion of all 11 completed envelopes downloaded as PDFs.
- Every client kept its number; the database refused to change a client number and to remove one from the record of
  numbers given.
- Billing Nest's letterhead was replaced by the corrected one. A Billing Nest BAA and Services Agreement generated on
  the server show one footer in one font and size on every page ("Page 1 of 7" ... "Page 7 of 7") and the full Laurel,
  Maryland address on every page.
- A new Extended Health Services envelope: the subject read "Please Sign: Business Associate Agreement and Services
  Agreement - Kilo Clinic", the email came from "Extended Health Services Inc." and said "Extended Health Services Inc.
  sent you documents to review and sign", the signing page and the Certificate of Completion named the company, and
  the preparer's own name appeared nowhere.
- A client with signed agreements was deleted (the confirmation named it), waited in the Trash with 30 days left while
  its signed envelope still opened under Agreements, and was restored with the same client number.
- In a browser over HTTPS on the updated server (24 checks): the BAA / Services Agreement / NDA columns and the
  filters, ticking clients and **Mark as test**, deleting two clients with the confirmation, the Trash, restoring with
  the original number, a client's **More actions** (Cancel deletes nothing), the Signed agreements register's Services
  tab (a client whose agreement is out reads "Out for signature" with the date sent), and a phone-sized screen with no
  sideways scrolling; no browser errors.
- "To restore the pre-update copy by hand" (above) was tested as written: it put the database back exactly as it was
  before the update (all 39 tables identical), the previous release ran on it again, and the update was then repeated
  from there with the same results (99 checks).
- The same update was also run from the deployed release and from an older one (0007) against PostgreSQL 16 with
  every envelope state, with the same result. The 254 automated tests passed. All 24 agreements (BAA, Services
  Agreement and Mutual NDA for each of the eight companies) were checked page by page for the footer. In a browser:
  the automatic subject naming both agreements, typing your own (**Yours**) and going back, the new field design
  with the company's saved signature already on the agreement, and the editor on a phone-sized screen.

**The November 6, 2026 update, tested from this ZIP's predecessor on the same Docker host**, over the release deployed
then (fea86f9) holding 7 clients, 5 completed envelopes and 3 envelopes left mid-way: one sent and not opened, one signed by
the client and waiting for the company, one opened by the client (consent given, signature adopted) and left. The steps
in "Updating to a new release" were followed exactly (the old folder kept under a dated name):
- `backup` and `update` finished in about a minute; the database went from 0007 to 0009 and the pre-update copy was
  saved.
- 34 of the 36 tables were identical row for row afterwards; the other two gained the five companies and the counter
  of client numbers. The 7 clients were numbered CL-0001 to CL-0007, oldest first.
- All three envelopes left mid-way were finished on the new version from the links emailed before the update (the
  opened one remembered the consent) and completed; every signed copy and Certificate of Completion, old and new,
  downloaded as a PDF.
- The five companies appeared with their letterheads, logos, addresses and governing law; generating for House of RCM
  asked for its signatory first, and once it was set, a BAA and Services Agreement with Patient Help Desk,
  Credentialing and Demographics Updates generated on its letterhead with its Austin address and Texas law.
- The same update was also run from two older releases (0005 and 0006) against PostgreSQL 16 with every envelope
  state, with the same result. In a browser: the eight companies in Create agreement, the letterhead check, the new
  services, the custom-service preview, a 60-character signer title on a phone and a desktop, the clients register,
  a client's record, adding a past DocuSign agreement, importing previous clients and the Signed BAA register.

**Version 5, tested from this ZIP on the same Docker host:**
- **Update from version 4.2** (the release installed on your server, 25460c4), holding two accounts, three clients,
  generated agreements, a completed envelope with replies, a signature installed with `./agreements.sh signatures`
  and three envelopes still waiting: backup and restore test, then `./agreements.sh update` (6 minutes; the
  application image is rebuilt once). Afterwards both people signed in with their own passwords; accounts, clients,
  every generated file, the completed envelope, its history and its signed PDFs were identical. The envelope waiting
  only on a viewer completed within a minute, and the signer and the viewer received the completed document and
  certificate. The automatic 30-day expiry was removed; an expiry the sender chose (10 days) was kept. A second update
  (version 5 to version 5) took 37 seconds and changed nothing.
- **Version 5 features on the updated server, over HTTPS (39 checks):** the default signatories and titles; the
  installed 24-7 Consultancy signature still printed; a phone photo of a signature saved in Company settings had its
  grey paper removed and printed on a Scribe Align BAA with Naeem Abbas, Chief Executive Officer; a user could not
  change company settings or open the activity log. The client's generated BAA was offered when sending; fields were
  placed automatically (signature, date and initials on all 6 pages); the envelope had no expiry; the email came from
  "Scribe Align LLC" in its website colours with the Help address; the client signed, and the envelope completed
  without the viewer opening it, then the client, the viewer and the copy recipient received the documents and the
  certificate. Home, Insights, an Excel and a CSV report, and the activity log (with IP address and browser, and
  signer actions) worked, and the activity log downloaded to Excel.
- **In a browser over HTTPS:** a signature photo uploaded in Company settings; a client's profile, **Send documents
  for signature**, the BAA added from the library, a viewer and a copy recipient added, fields placed automatically,
  sent; the client opened the emailed link, the date was filled in, a style and a different initials style were
  adopted, every field filled with **Start** / **Next**, **Finish**; the signed PDF carries the company signature on
  its line, the client's signature stamp, the date and initials on every page. Every screen, with no browser errors.
- **Fresh install** of this ZIP: all of the original checks above passed again (58).
- **The real signatures** of Naeem Abbas and Danish Ather (supplied separately; never part of this package), uploaded
  under Admin > Company settings on that server, printed on the company signature line of a Scribe Align, an Extended
  Health Services and a 24-7 Consultancy agreement generated in the container, with no warning: up to 0.96 inch tall,
  on the line, clear of the company name, the execution block on one page. Their sizes were then approved on the
  signature pages of all nine agreements (BAA, Services Agreement and Mutual NDA for each company) and made the
  defaults: Naeem Abbas 0.74 × 0.58 inch (60 %), Danish Ather 0.89 × 0.82 inch (85 %).
- **This ZIP, with the approved sizes:** updated from version 4.2 (25460c4) holding data (43 seconds with the images
  built): accounts, clients, generated files and the completed envelope identical, the database at its latest version,
  and the 39 version 5 checks passed; the real signatures uploaded there printed on all nine agreements at exactly
  0.74 × 0.58 inch (Naeem Abbas) and 0.89 × 0.82 inch (Danish Ather). Updated from the earlier version 5 ZIP: a
  signature saved there before the update was kept and now prints at 60 % instead of full size, all other data
  identical. Fresh install: the 58 original checks passed and every screen opened without a browser error.
  Reloading any screen in the browser now always shows it (a reload could leave a screen empty before).
- **Screenshot tour of every feature** (on a server installed from this ZIP, with fictional data: four people, six
  clinics, envelopes in every state, templates and a bulk send) found and fixed: a second copy of a client created
  when an Admin or the Super Admin worked on a team member's client (bulk send, New client, agreements saved by
  name); the Templates list squeezed into a narrow column; the Home Start menu and the recipient role and Customize
  menus cut off; "Opened a client profile" logged on every tab switch (now once per visit). After the fixes, every
  screen, menu and dialog opened without a browser error, the client signed on a computer and on a phone, and a fresh
  install passed the 58 original checks.
- Memory after these runs: about 1.4 GB (the virus scanner 0.95 GB).

**Not tested:** the Gmail verified tick and logo (they need your DNS records and a Verified Mark Certificate; the Email
trust screen checks the records). Real Google Workspace mailboxes (app passwords, the service-account method and Gmail's own limits);
check them with Send test email and Check inbox in step 5. How Gmail, Outlook and Apple Mail display the branded email
(it was checked in a browser): send yourself a test envelope and look at it in the mail programs your clients use. A real Let's Encrypt certificate with your DNS. Ubuntu
itself (the installer's Docker installation and `ufw` step). The off-site S3 copy, restoring on a new server, and
months of real use.
