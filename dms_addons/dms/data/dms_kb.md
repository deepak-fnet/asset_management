## What the DMS is
The Document Management System (DMS) keeps every controlled document of the company in one place: approved upload,
a repository with folder and file access, approved revisions, cut-off, archive, and approved disposal. Files are stored
on the DMS server (SFTP), optionally inside AES-256 password ZIPs. Odoo keeps the records, the history and the access.
Main screens (left menu): Home (your folders), Dashboard, Ask DMS, All documents, Access requests, To approve,
My documents, Checked out, Starred, Delete requests, Groups; at the bottom Approvers, Disposal, Audit log (Controllers).
The menu folds with the « button or the [ key; Ctrl+K opens the command palette.

## Roles
- DMS Member (group "DMS / User"): opens the DMS app; sees only folders and files shared with them.
- Document Controller (group "DMS / Document Controller"): sees and edits every folder, decides access and folder
  deletion requests, retires and permanently deletes documents, runs disposal, sees the Organisation dashboard,
  unusual activity, the audit log and every setting. Being Controller does not make you an approver.
- Approvers are set per folder: L1 Reviewer, L2 Approver, L3 Final Authority (L3 only for document types that need it).
  Set them in Approvers (Configuration > Folders). Sub-folders inherit them.
- Department Head (set on the department folder) co-approves disposal.
- Owner: the person who uploaded a document. They always see it unless a "No access" line excludes them.

## Access levels and how access works
| Level | What it allows |
|---|---|
| View | open and read the file inside Odoo (protected viewer, no copy, no screenshot, watermark) |
| View & Download | View + download a copy (password protected if downloads are protected) |
| Edit | View & Download + upload, check out / check in, move, rename folders, share, request folder deletion |
| No access | used on one file to exclude someone who has access through the folder |
- Access given on a folder applies to every sub-folder and file inside it.
- Access can be given to a person or to a DMS Group (a named set of people + folders + level).
- A single file can have its own lines (Access tab of the document): a different level, or No access.
- Effective level = the best of: folder grants (on the folder or a parent), group grants, file lines; a file "No access" line wins.
- Archive folders are read only: whatever the grant, the level there is View.
- Controllers always have Edit.
- Who can see who has access: the Access tab of a document (and Ask DMS) is shown to the Controller, the owner and people with Edit.

## Buttons and who sees them
| Button / action | Where | Who sees it |
|---|---|---|
| Upload | top left, or drop a file on a folder | Edit on the folder |
| New folder | Home / inside a folder | Controller at the top level; Edit inside a folder |
| Who has access (share a folder) | inside the folder: "Folder" button → Who has access | Edit on the folder, or the Controller |
| Rename folder | inside the folder: "Folder" button → Rename | the Controller, or whoever created that folder and has Edit; department folders cannot be renamed |
| Request deletion (folder) | inside the folder: "Folder" button → Delete | Edit on the folder (not department folders); the Controller decides in Delete requests |
| Open / View | document panel, lists | View or better |
| Download | document panel, lists | View & Download or better |
| Check out to edit | document panel | Edit, document published, nothing pending |
| Check in new version | document panel | the person who checked it out |
| Cancel check-out / Force unlock | document panel | the holder / the Controller |
| Send for approval | revision | the author or the Controller, revision in draft and AI check passed |
| Approve / Refuse | revision, To approve, bell notification, email | only the approver whose turn it is |
| Bring cut-off forward | superseded revision | an approver of the folder or the Controller |
| Move | document panel | Edit on the document and on the target folder |
| Retire | document panel | Controller only |
| Delete permanently (trash) | document panel | Controller only |
| Share (person icon) | document panel | Edit |
| Request access | All documents, Ask DMS | anyone who can see the title but not open it |
| Give access / Refuse (requests) | Access requests | Controller, or Edit on the document's folder |
| Star | everywhere | anyone who can open it |

## Upload and the AI check
1. Press Upload (or drop a file on a folder). Choose the file: the form locks while the AI works.
2. The AI reads the file (text, or OCR for scans and photos), picks the document type, folder, title and description
   (fields marked AI), and warns about personal data (Aadhaar, PAN, bank numbers) and near-duplicates.
3. If the type has AI rules (e.g. Invoice has 26), the rule check runs: each rule passes or fails with a reason.
   Upload is allowed only when every must-pass rule passes. Calculation rules add amounts in Python; duplicate rules
   compare invoice number, GSTIN, date and total with stored documents.
4. Upload as draft, or Upload and send for approval. The document gets its number, e.g. ACC-INV-001
   (folder code - type code - running number; numbers are never reused).
Rules are kept per document type: Configuration > Document Types > Upload rules.

## Approval workflow
Draft → Sent for approval → L1 Reviewer → L2 Approver → (L3 Final Authority) → Published (Rev 0).
- Each step: the approver gets an email and a bell notification with Approve / Refuse buttons; the author is told at each step.
- A refusal needs a reason; a first upload goes back to the author as Refused, a new revision is dropped.
- While in review nobody can check the document out; readers keep seeing the published revision.
- The approver sees an AI summary, risk points and what changed since the previous revision.

## Revisions, cut-off and archive
1. Check out to edit (Edit access, published document) — the document is locked to you.
2. Download, edit on your computer.
3. Check in new version — same file type — it goes for approval.
4. When approved it becomes the current revision (Rev 1, 2 …). The old one is Superseded and stays visible until its
   cut-off date (Settings: cut-off days, default 30), then the daily job moves it to the Archive folder.
An approver can bring the cut-off forward for an urgent change.

## Retire, archive, retention and disposal
- Retire (Controller): the whole document goes to the Archive, read only.
- Each type has a retention period after archive (days). When it ends, the Controllers get a "Retention ended" mail.
- Disposal request (Disposal menu): pick archived files past retention → submit → both the Controller and the
  Department Head approve → files are destroyed on the server, a log is kept.
- Delete permanently (Controller): removes the document, every revision and its files at once (trash button).

## Access requests
Anyone who sees a document's title but cannot open it presses Request access, picks View / View & Download / Edit and
gives a reason. Controllers and people with Edit on the folder get an email and a bell notification with Grant / Refuse;
any one of them deciding closes it for the others. The requester is told the result.

## Viewing, downloading and passwords
- Open shows the file inside Odoo in the protected viewer (PDF, Word, Excel, PowerPoint, images, text, video, audio, ZIP).
  Copy, print, right click and screenshots are blocked and every view is logged. It never asks a password.
- Download needs View & Download. If downloads are protected (Settings > Download password), PDF and Office files come
  with that password, other files inside a password ZIP. People with download access can ask Ask DMS for it.
- Server file protection (Settings > Protect files on the server) keeps the stored copies in AES-256 ZIPs with a
  different password, which is never shown to anyone.
- A file that already had its own password when uploaded keeps it: ask the person who uploaded it.

## Search
- Search box: words in number, title, description; press Enter or AI to let the AI understand a plain question
  (vendor names, dates, Tamil). Voice search with the mic (Tamil or English).
- Ask DMS: questions in plain language, screenshots (Ctrl+V), counts, status, access, how-to. It searches by meaning
  (vector index) and checks access for every document.
- Ctrl+K: command palette to jump to documents, folders and actions.

## Dashboard
- Mine: to approve, my documents, rejected, expiring, checked out, recent documents, my activity heatmap, my to-do.
- Organisation (Controllers): counts, uploads, active people, unusual activity, expiring, access requests, personal
  data, duplicates, approval time, storage; Activity tab (heatmap by hour, what people did, who is busy, timeline);
  Unusual activity tab (bulk downloads, access outside working hours, mass deletes, permission spikes, access fishing,
  forced unlocks); Needs attention (expiring, late approvals, personal data, duplicates, AI rule failures, stale check-outs).

## Notifications and emails
- Every event sends an email and a bell notification (top bar bell, floating bell you can drag, live toasts).
- Approval notifications have Approve / Refuse / Mark as done buttons; the panel shows who else must act and tracks
  seen / opened / done for each person.
- Configuration > Emails & Notifications: for each of 34 events choose Email and/or In-app, and send yourself a sample.
- Daily jobs: approval reminders (after the set days) and escalation to Controllers, check-out reminders, expiry
  reminders, retention ended, unusual activity mail to Controllers.

## AI features
- Upload: type/folder/title filled by AI, rule check before upload, personal data and duplicate warnings.
- After storing: summary and risk points for the approver, important dates (expiry, renewal, due) with reminders,
  revision comparison, sensitive data detection, near-duplicate detection.
- Search: AI search box, voice, Ask DMS with meaning search and screenshot recognition.
The AI server and model are set in Settings > AI (OpenAI-compatible, e.g. vLLM with Qwen).

## Settings (DMS > Configuration > Settings)
DMS server (IP, port, user, password, root path, Test connection), cut-off days, theme (navy, brown, gold), AI server,
model and key, file protection on the server and its password, download protection and its password, unusual activity
thresholds (bulk downloads, views, working hours, approval and check-out days), expiry reminder days, email on/off,
meaning search service URL and Rebuild meaning search.

## Data model (for technical questions)
| Model | Holds |
|---|---|
| dms.folder | folders, department, approvers L1/L2/L3, department head, access lines |
| dms.access | one access line: folder or document, person or group, level |
| dms.group | DMS groups: members, folders, level |
| dms.document | a document: number, title, type, folder, owner, state, current revision, check-out, expiry |
| dms.revision | one version of the file: rev number, state, file on the server, AI results, insights, cut-off, archive |
| dms.approval | one approval step of a revision: level, approver, state, comment, date |
| dms.document.type | types (Invoice, Record, Form …), code, L3 needed, retention days, AI rules |
| dms.ai.rule | one upload rule of a type (AI, calculation or duplicate) |
| dms.access.request | an access request and its decision |
| dms.disposal | a disposal request |
| dms.log | the audit log: every view, download, upload, approval, access change |
| dms.notify.rule | email / in-app setting of each event |
| smart.notification | bell notifications and their tracking per person |
Document states: Draft, In Review, Published, Refused, Archived, Discarded. Revision states: Pending, In review, Current,
Superseded, Archived, Refused, Dropped, Discarded.

## What a DMS Member can and cannot do
| A member with … | Can | Cannot |
|---|---|---|
| no access to a folder | see published document titles in All documents and Ask DMS, press Request access | open, download, or see drafts |
| View | open and read files inside Odoo, star them | download, upload, check out, share, move |
| View & Download | View + download a copy | upload, check out, share, move |
| Edit | upload, new sub-folders, check out / check in, move, share (Who has access), request folder deletion | retire, delete permanently, decide access requests of other folders, see the audit log |
Every member: approves only when they are the approver of that step; sees only their own Ask DMS chats and their own dashboard.
Only the Document Controller: retire, delete permanently, disposal, Groups, Approvers setup, Settings, Audit log, Organisation dashboard.

## How to: give someone access to a whole folder
1. Open the folder (Home or the Folders list on the left).
2. Press the "Folder" button at the top of the folder → Who has access.
3. Search the person (or a DMS Group), pick View, View & Download or Edit, press Give access.
4. Everything inside the folder and its sub-folders is now open to them. To keep one file away from them, open that file → Access tab → set them to No access.
Alternative for many people: Groups (left menu, Controllers) → make a group with the people and the folders.

## How to: share only one file
Open the document → the person icon (Share this file only) → pick the person and level. Or in the Access tab change one person's level for this file only.

## How to: change approvers of a folder
Controllers: left menu bottom → Approvers (or Configuration > Folders) → open the folder → set L1 Reviewer, L2 Approver, L3 Final Authority and Department Head. Sub-folders use their parent's approvers unless they have their own.

## How to: upload a new version of a published document
Open it → Check out to edit → Download → edit → Check in new version (same file type) → it goes for approval → when approved it becomes the next revision; the old one stays visible until its cut-off date and is then archived (it stays readable in the Archive).

## How to: find a document
Type words in the search box (number, title, description); phrases are also matched by meaning (✨ by meaning). Ask DMS answers plain questions and recognises screenshots. Ctrl+K jumps anywhere.
