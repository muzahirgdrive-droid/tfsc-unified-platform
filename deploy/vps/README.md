# TFSC platform on one server (VPS)

This package installs the whole platform on one Linux server with Docker: the Sales CRM, TFSC Agreements (with
electronic signature, sent from each company's compliance mailbox), company sign-in (SSO with Keycloak), file storage
with a virus scan of every upload, and encrypted backups with a nightly restore test. HTTPS certificates are obtained and renewed automatically (Caddy with Let's Encrypt).

## What you need

| | |
|---|---|
| Server | Ubuntu 24.04 or 22.04 LTS, x86_64, **4 vCPU, 8 GB RAM, 80 GB SSD** or more. Measured on a test install: about 2.4 GB in use with everything idle (virus scanner 0.95 GB, sign-in service 0.58 GB); document generation, the nightly restore test and virus-signature updates add to that. |
| Access | SSH as root (or a user with `sudo`) |
| Domain | A domain you control, with four addresses pointing at the server (step 1) |
| Email | An SMTP account to send invitations and password resets (host, port, user, password or app key, sender address) |
| Safe place | A password manager for the generated secrets |
| Mailboxes | The three compliance mailboxes (Google Workspace): compliance@scribealign.com, compliance@extendedhealthservices.us, compliance@24-7consultancyllc.com, each with an app password (step 5) |
| Later | The signature images for issued agreements; optionally an S3 bucket elsewhere for off-site backup copies |

## 1. Point four addresses at the server

At your DNS provider, create four **A records** with the server's public IP address (example for `example.com`):

| Name | Used for |
|---|---|
| `crm.example.com` | the CRM |
| `agreements.example.com` | TFSC Agreements |
| `sso.example.com` | sign-in, password reset, authenticator app |
| `files.example.com` | short-lived download links for exports |

If your provider has a firewall in its control panel, allow incoming TCP **22, 80 and 443**. The installer opens 80
and 443 in `ufw` when it is active. Everything else stays closed: the apps listen on the server itself only.
Outgoing connections to `smtp.gmail.com:587` and `imap.gmail.com:993` must be allowed for the compliance mailboxes
(some providers block outgoing mail ports on new accounts until you ask them to open them).

## 2. Copy this ZIP to the server and unpack it

```
scp TFSC-VPS-Deploy-*.zip root@YOUR_SERVER_IP:/opt/
ssh root@YOUR_SERVER_IP
apt-get update && apt-get install -y unzip
cd /opt && unzip TFSC-VPS-Deploy-*.zip && mv TFSC-VPS-Deploy-*/ tfsc && cd tfsc
```

## 3. Install

```
sudo ./tfsc.sh install
```

It asks for your domain, the SMTP settings and the time zone of the nightly backup. Then it installs Docker if it is
missing (it asks first), generates every password and key, builds the application images (5 to 15 minutes the first
time), starts everything, checks the four HTTPS addresses and prints two one-time **setup keys**.

**Right away:** copy `/opt/tfsc/infra/compose/.env` into your password manager. It holds `BACKUP_PASSPHRASE` (the
only key to the encrypted backups) and the sign-in admin password (`KC_ADMIN_PASSWORD`).

If an address shows `NOT YET`, DNS does not point at the server yet or port 80/443 is blocked; Caddy keeps trying.
Check again with `./tfsc.sh status`.

Unattended install (no questions): `DOMAIN=example.com SMTP_HOST=smtp.example.com SMTP_PORT=587 SMTP_USER=... SMTP_PASSWORD=... SMTP_FROM=no-reply@example.com sudo -E ./tfsc.sh install --yes`

## 4. Create the first Super Admin accounts

1. Open `https://crm.example.com/login`. The page asks for the **CRM setup key**: enter it with your name, email and a
   password. You are now the CRM Super Admin.
2. Open `https://agreements.example.com/login`. Enter the **Agreements setup key** with your name, email and a password.
3. On the server: `sudo ./tfsc.sh link-admins`. This lets the CRM hand clients to Agreements under your account.
4. In the CRM: **People & Teams** > select yourself > **Send sign-in invitation**. Open the email, choose your sign-in password
   and add the authenticator app (required for Supervisors and above).
5. Sign out of the CRM and sign in again with **Sign in with company SSO**. Once that works:
   `sudo ./tfsc.sh finish-setup`. From then on everyone signs in with SSO; only your setup password still works as an
   emergency sign-in for the Super Admin (for example if the sign-in service is down), so keep it in the password manager.

Then add your team in **People & Teams** (each person gets an invitation email) and, on their profile under **Agreements access**,
choose who may use Agreements. The setup keys are shown again with `./tfsc.sh setup-keys` until they are used.

## 5. Connect the company compliance mailboxes

Agreements sends signing requests, reminders and completed documents from each company's compliance mailbox, and reads
that inbox (read-only) for client replies and bounce notices, which appear on the envelope. All three domains use
Google Workspace. Do this once for each of compliance@scribealign.com, compliance@extendedhealthservices.us and
compliance@24-7consultancyllc.com:

1. Sign in to Google as that mailbox. Under Google Account > Security, turn on **2-Step Verification**.
2. Open https://myaccount.google.com/apppasswords, create an app password named "TFSC Agreements" and copy the
   16 characters.
3. In Agreements: **Company mailboxes** > the company > "Send from this company's own mailbox", sign-in method
   **App password**, paste it, keep "Read replies and bounce notices" on, and **Save**. The server settings for
   Google Workspace are filled in.
4. Select **Send test email**, then **Check inbox**. Both must report success; the message shows the real reason if not.
5. If the company signs electronically (instead of the saved signature image), enter the signer's email
   (Naeem Abbas or Danish Ather) in "Company signer's email".

If your Google Workspace administrator has turned off app passwords, use **Google Workspace service account** instead:
create a service account and a JSON key in Google Cloud, then in the Google Admin console (Security > Access and data
control > API controls > Domain-wide delegation) add its client ID with the scope `https://mail.google.com/`, and
upload the JSON key on the Company mailboxes screen.

Sending: generate the agreements, then select **Send for signature**. The client's signature, name, title and date
fields are placed for you; add initials, more recipients or other fields, then **Send**. Follow progress under
**Signatures**. Google limits each Workspace mailbox to about 2,000 emails a day.

## 6. Signature images

Put the four files (`Naeem Abbas.png`, `Naeem Abbas.json`, `Danish Ather.png`, `Danish Ather.json`) in a folder on the
server, then:

```
sudo ./tfsc.sh signatures /root/signatures
```

Delete that folder after you have checked one issued agreement. The images are never part of this package.

## 7. Backups

- Automatic: every night at 2:00 in your time zone (full backup weekly, changes daily, 14 days kept), every database
  change within a minute in between, all encrypted. After each backup a restore test rebuilds a copy and checks it.
  Results: CRM > **Security** > **Backups**, or `./tfsc.sh status`.
- Take one now (for example before an update): `sudo ./tfsc.sh backup`.
- **Off-site copy (strongly recommended):** the backups above are on the same server. Create a bucket at another
  provider (any S3-compatible storage), copy `infra/compose/backup-offsite.env.example` to `backup-offsite.env` in the
  same folder, fill it in, and run `sudo ./tfsc.sh start`.
- Restoring after a disaster: `docs/runbooks/backups-and-restore.md`. It needs `BACKUP_PASSPHRASE`.

## Everyday commands

| Command | What it does |
|---|---|
| `./tfsc.sh status` | what is running, the HTTPS addresses, the latest backups |
| `./tfsc.sh logs` or `./tfsc.sh logs crm-api` | follow the logs (Ctrl+C to stop) |
| `sudo ./tfsc.sh backup` | full backup now |
| `sudo ./tfsc.sh restart` / `stop` / `start` | restart, stop or start everything |
| `./tfsc.sh help` | every command |

## Updating to a new release

```
cd /opt && unzip TFSC-VPS-Deploy-NEW.zip
cp tfsc/infra/compose/.env TFSC-VPS-Deploy-NEW/infra/compose/        # and backup-offsite.env if you use it
mv tfsc tfsc-previous && mv TFSC-VPS-Deploy-NEW tfsc && cd tfsc
sudo ./tfsc.sh backup && sudo ./tfsc.sh update
```

Data lives in Docker volumes and is kept; database changes are applied automatically during the update.

## Changing settings later

- **Mail server:** the installer's SMTP settings are loaded into the sign-in service on its first start only. To change
  them: `https://sso.example.com/admin`, user `admin`, password `KC_ADMIN_PASSWORD` from `.env` > realm **tfsc** >
  **Realm settings** > **Email**.
- **Other settings** in `infra/compose/.env`: edit, then `sudo ./tfsc.sh start`.
- **Never change** `POSTGRES_PASSWORD`, `BACKUP_PASSPHRASE`, `CRM_FIELD_KEY` or `CRM_PHONE_KEY_SECRET` after the install:
  the database and the encrypted fields depend on them.
- `TFSC_SECRET_KEY` encrypts the company mailbox passwords. Releases from October 2026 add it on `update` if it is
  missing. If it changes, enter the mailbox passwords again under Company mailboxes.

## When something is wrong

| Symptom | Check |
|---|---|
| A company mailbox shows a problem, or signing emails stay "Retrying" | Agreements > Company mailboxes > Send test email shows the reason (wrong app password, 2-Step Verification off, outgoing port 587 blocked by the provider) |
| An address shows `NOT YET` or the browser warns about the certificate | DNS points at this server (`getent hosts crm.example.com`), ports 80/443 open at the provider; `./tfsc.sh logs edge` |
| Invitation or reset emails do not arrive | SMTP settings (see above); `./tfsc.sh logs keycloak`; the spam folder |
| Pages are slow or services restart | memory: `docker stats --no-stream`; 8 GB is the minimum |
| Anything else | `./tfsc.sh status`, then `./tfsc.sh logs <service>` |

## What was tested, and what was not

Tested on 2026-10-21 by installing this ZIP with `tfsc.sh install` on a fresh Docker host (4 vCPU, 16 GB) with test
addresses (`*.tfsc.test`), certificates from Caddy's own test authority instead of Let's Encrypt, and a mail catcher
instead of a real mail server:

- install unattended in about 4 minutes on that host (base images already downloaded); only ports 80 and 443 reachable from outside; all three HTTPS
  addresses answering; the first encrypted backup and its restore test passed on their own;
- both setup keys work once; `link-admins`; the invitation email; setting a password and the authenticator app; CRM
  sign-in with SSO (password + code); an agent invited, signed in to the CRM and to Agreements with SSO; giving the
  agent Agreements access;
- `finish-setup`: password sign-in refused for everyone but the Super Admin's emergency password;
- `signatures` (with placeholder files), `backup`, `status`, and `update` from one release to the next with the steps
  above: data kept, everyone still signs in.

Electronic signature was tested on 2026-10-22 against a test mail server (GreenMail, SMTP and IMAP) and in a browser:
sending from the company mailbox, signing in order (client, then company signer on a phone), the completed documents
and Certificate of Completion emailed to everyone, a client's emailed reply with a PDF attached to the envelope, a
bounce marking the recipient undeliverable, and correcting the address.

Not tested from here: signing in to Gmail itself with an app password or a service account (use Send test email and
Check inbox on your server), real Let's Encrypt certificates (they need public DNS pointing at a real server), a real mail
provider, Ubuntu itself and the installer's Docker installation step (Docker was already present), a specific hosting
provider's firewall, and load on a server of the recommended size (the measured load
results are in `docs/PERFORMANCE.md`).

More detail: `infra/README.md` (all services and options), `docs/runbooks/` (cutover, backups, legacy workbook import).
