# TFSC Agreements on one server (VPS)

This package installs **TFSC Agreements only** on one Linux server with Docker:
- agreement generation (BAA, Services Agreement, Mutual NDA) on each company's letterhead;
- electronic signature sent from each company's compliance mailbox;
- user accounts and permissions;
- encrypted backups with a nightly restore test;
- a virus scan of files that clients attach to their email replies;
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
| Later | The signature images for issued agreements; optionally an S3 bucket elsewhere for off-site backup copies |

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
2. **Users & permissions** > **Create user**: name, email, role (User, Admin or Super Admin) and the companies the
   person may prepare agreements for. The screen shows a temporary password; give it to the person privately. They
   choose their own password at first sign-in. Use **Reset password** there if someone forgets theirs.

## 5. Connect the company compliance mailboxes

Agreements sends signing requests, reminders and completed documents from each company's compliance mailbox, and reads
that inbox (read-only) for client replies and bounce notices, which appear on the envelope. All three domains use
Google Workspace. Do this once for each of compliance@scribealign.com, compliance@extendedhealthservices.us and
compliance@24-7consultancyllc.com:

1. Sign in to Google as that mailbox. Under Google Account > Security, turn on **2-Step Verification**.
2. Open https://myaccount.google.com/apppasswords, create an app password named "TFSC Agreements" and copy the
   16 characters.
3. In Agreements: **Company mailboxes** > the company > "Send from this company's own mailbox", sign-in method
   **App password**, paste it, keep "Read replies and bounce notices" on, and **Save**. The Google Workspace server
   settings are filled in.
4. Select **Send test email**, then **Check inbox**. Both must report success; the message shows the real reason if not.
5. If the company signs electronically (instead of the saved signature image), enter the signer's email
   (Naeem Abbas or Danish Ather) in "Company signer's email".

If your Google Workspace administrator has turned off app passwords, use **Google Workspace service account** instead:
create a service account and a JSON key in Google Cloud, then in the Google Admin console (Security > Access and data
control > API controls > Domain-wide delegation) add its client ID with the scope `https://mail.google.com/`, and
upload the JSON key on the Company mailboxes screen.

## 6. Send agreements for signature

1. **Create agreement**: choose the company, enter the client, choose BAA, Services Agreement and/or Mutual NDA, the
   services and the terms. The governing state starts at the company's state (Scribe Align Nevada, Extended Health
   Services Missouri, 24-7 Consultancy Georgia) and applies to every agreement in the package. **Generate**.
2. Select **Send for signature**. The client's signature, name, title and date fields are already placed. Add initials
   on every page, more recipients (who sign, need to view or receive a copy, in a signing order) or other fields,
   then **Send**.
3. Follow progress under **Signatures**: who has viewed and signed, email delivery, client replies, the history.
   Use Resend, Correct (a wrong email address) or Void. When everyone has signed, everyone receives the completed
   documents and the Certificate of Completion by email, and the compliance mailbox gets a copy.
4. Anyone can check a completed PDF at `https://agreements.yourcompany.com/verify`.

## 7. Signature images

Put the four files (`Naeem Abbas.png`, `Naeem Abbas.json`, `Danish Ather.png`, `Danish Ather.json`) in a folder on the
server, then:

```
sudo ./agreements.sh signatures /root/signatures
```

Delete that folder after you have checked one generated agreement. The images are never part of this package.

## 8. Backups

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

Data lives in Docker volumes and is kept; database changes are applied automatically during the update.

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
- **Backups:** `./agreements.sh backup` made a full encrypted backup in 12 seconds, then the restore test rebuilt the
  database at a point in time and checked it; `status` lists both.
- **Update:** from the previous build of this package, following "Updating to a new release": accounts, clients,
  envelopes, their histories and the signed PDFs were identical afterwards (backup and update took 41 seconds with the
  images built).

**Not tested:** real Google Workspace mailboxes (app passwords, the service-account method and Gmail's own limits);
check them with Send test email and Check inbox in step 5. A real Let's Encrypt certificate with your DNS. Ubuntu
itself (the installer's Docker installation and `ufw` step). The off-site S3 copy, restoring on a new server, and
months of real use.
