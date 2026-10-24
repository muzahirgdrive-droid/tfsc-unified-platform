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
| Server | Ubuntu 24.04 or 22.04 LTS, x86_64, **2 vCPU, 4 GB RAM, 40 GB SSD** or more. Measured in testing: about 1.6 GB of memory in use after a complete signing run, 0.95 GB of it the virus scanner |
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
by Gmail, not by this application: it needs SPF and DKIM passing, DMARC at enforcement (`p=quarantine` or `p=reject`),
the logo as an SVG Tiny PS file, a **Verified Mark Certificate** (from DigiCert, Entrust or GlobalSign, which needs a
registered trademark for the logo) and a BIMI record. Until then, set each compliance mailbox's Google profile photo
to the company logo: Gmail shows it to many recipients right away.

If your Google Workspace administrator has turned off app passwords, use **Google Workspace service account** instead:
create a service account and a JSON key in Google Cloud, then in the Google Admin console (Security > Access and data
control > API controls > Domain-wide delegation) add its client ID with the scope `https://mail.google.com/`, and
upload the JSON key on the Company mailboxes screen.

## 7. Clients

**Clients** lists every client you can see. A client's profile has:
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
   Choose the company, and the client if there is one. **Documents**: drag files from your computer (PDF, Word: DOCX,
   DOC, ODT, RTF; text, PNG or JPEG; everything is converted to PDF), or pick the client's generated agreements and
   files. Or create the agreements first: **Create agreement** (company, client, BAA, Services Agreement and/or Mutual
   NDA, services and terms; the governing state starts at the company's state), **Generate**, then **Send for
   signature**.
2. **Recipients**: one card per person with name, email and a role (**Needs to sign**, **Signs in person**, **Needs to
   view**, **Receives a copy**). The client's signatory is filled in. **Customize** adds an access code or a private
   message for that person; the bin removes them; **Add recipient** adds another. Turn on **Set signing order** to
   number the people (the same number means at the same time); without it everyone receives the email at once.
3. **Fields** are placed for you: the signature, name, title and today's date on the signature block, required fields,
   and initials on every page in a free spot near the bottom. Drag any field to move it, select it to change or delete
   it, or drag new ones from the palette. **Auto-place fields** places them again; **Initials on every page** adds
   initials for the selected person.
4. **Options**: the documents **never expire** unless you choose an expiry; reminders; attaching the completed
   documents to the final email; whether recipients may assign the documents to someone else. **Send**.
5. The client receives an email in the company's colours from the company's compliance mailbox, with a **REVIEW
   DOCUMENT** button. On the signing page they accept the electronic record disclosure, follow **Start** / **Next** to
   each field (today's date is already filled in), and adopt a signature and initials: one of 16 styles made from their
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
9. Anyone can check a completed PDF at `https://agreements.yourcompany.com/verify`.

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
| `./agreements.sh status` | what is running, the HTTPS address, the latest backups |
| `./agreements.sh logs` or `./agreements.sh logs agreements-worker` | follow the logs (Ctrl+C to stop) |
| `sudo ./agreements.sh backup` | full backup now, then the restore test |
| `sudo ./agreements.sh restart` / `stop` / `start` | restart, stop or start everything |
| `./agreements.sh help` | every command |

## Updating to a new release

```
cd /opt && unzip TFSC-Agreements-Deploy-NEW.zip
cp tfsc-agreements/deploy/agreements/.env TFSC-Agreements-Deploy-NEW/deploy/agreements/   # and backup-offsite.env if you use it
mv tfsc-agreements tfsc-agreements-previous && mv TFSC-Agreements-Deploy-NEW tfsc-agreements && cd tfsc-agreements
sudo ./agreements.sh backup && sudo ./agreements.sh update
```

Data lives in Docker volumes and is kept: users, clients, agreements, envelopes, signatures, mailboxes and backups.
Database changes are applied automatically during the update.

The update rebuilds the application (about 6 minutes in testing when its libraries change, under a minute
otherwise) and keeps the site running until the new version starts. Updating to version 5 also:
- removes the 30-day expiry that earlier releases gave every envelope by default (an expiry someone chose is kept);
- prints company signatures at the approved sizes (Naeem Abbas 60 %, Danish Ather 85 %; adjustable per company);
- completes envelopes that were waiting only for someone who needed to view them;
- keeps signature images installed with `./agreements.sh signatures` until you save one under Admin > Company settings.

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
| Pages are slow or services restart | memory: `docker stats --no-stream`; 4 GB is the minimum |
| Anything else | `./agreements.sh status`, then `./agreements.sh logs <service>` |

## What was tested, and what was not

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
- Memory after these runs: about 1.4 GB (the virus scanner 0.95 GB).

**Not tested:** the Gmail verified tick and logo (they need your DNS records and a Verified Mark Certificate; the Email
trust screen checks the records). Real Google Workspace mailboxes (app passwords, the service-account method and Gmail's own limits);
check them with Send test email and Check inbox in step 5. How Gmail, Outlook and Apple Mail display the branded email
(it was checked in a browser): send yourself a test envelope and look at it in the mail programs your clients use. A real Let's Encrypt certificate with your DNS. Ubuntu
itself (the installer's Docker installation and `ufw` step). The off-site S3 copy, restoring on a new server, and
months of real use.
