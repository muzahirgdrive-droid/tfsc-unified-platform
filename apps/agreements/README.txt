TFSC COMPLIANCE 4.0.0
Complete standalone application

WINDOWS: OPEN THE WEB PAGE
1. Extract the entire ZIP to a writable folder, such as C:\TFSCCompliance.
2. Double-click Start TFSC Compliance.cmd.
3. On first use, open .data\setup-key.txt and enter that key on the setup page.
4. Create your own Super Admin account. Then sign in and create Admins/Users.

Python and dependencies are included. Activated desktop Microsoft Word is needed
for PDF export on Windows. index.html is the interface source; start the launcher
instead of opening index.html directly. Stop TFSC Compliance.cmd stops the app
after the current export finishes.

VPS: Give this complete folder to your developer and follow DEPLOYMENT.txt.
It includes Docker Compose, PostgreSQL, HTTPS proxy and a Linux document worker.

WHAT IS INCLUDED
- All three contracting companies and the supplied logo/letterheads/signatures.
- 19 service categories, including Personal Injury Collection (FTE or percentage).
- BAA and/or Services Agreement with editable Word and PDF output.
- Super Admin, Admin and User accounts; assigned users and company permissions.
- Searchable client history, retained contract versions and named-user activity.
- The same generated file bytes in the selected folder and in the database.
- Larger signatures on their lines and automatic Page X of Y numbering.
- An in-page folder browser that does not depend on a hidden Windows dialog.

EXISTING LOCAL RECORDS
Keep the original application .data directory and contract folders backed up.
After creating your Super Admin, use Client & contract history > Import existing
local contracts. This imports old client drafts and contract Records manifests
without modifying their files. Historical authors were not previously recorded.
This developer ZIP intentionally has no client records, accounts or passwords.
The authorized company signature images ARE included; share the ZIP privately.

FILES
README.txt: getting started
DEPLOYMENT.txt: Windows/VPS setup, backups and scaling
DEVELOPER.txt: code map and test instructions
VERIFICATION.txt: checks and measured capacity
TEMPLATE-NOTES.txt: retained wording and signature notes

Desktop mode can choose any accessible folder. On a VPS, folders are within
managed server storage; use authenticated downloads for copies on your computer.