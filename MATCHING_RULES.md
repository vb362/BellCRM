# Matching rules and proposal cards

These rules compare Bellhaven's website with the CRM and produce suggestions for human approval. The matching script saves proposals; it does not edit CRM accounts. Each card explains what was found, what may be happening, and why the suggested action fits.

## 1. How matching works

- The website supplies facility details. A listing on Bellhaven's website indicates Bellhaven ownership; an explicit website parent field is not present nor required.
- Search the whole CRM, including facilities under other parents. Exclude corporate parent accounts from facility matching.
- **Same name:** both normalized names are present and equal.
- **Same address:** normalized street, city, and state are present and equal. ZIP does not determine a match.
- Try address matching first. Only if no CRM address matches, try name matching.
- **Complete website record:** name, street, city, and state are present.
- Follow existing `chow_current_account` and `duplicate_of_account` links to the current account before matching. Exclude old accounts in the chain from matching and absence proposals.
- Same-name, same-address candidates enter duplicate resolution first. Other multiple candidates produce individual action cards, as explained in section 3.
- ZIP differences alone produce no proposal. ZIP remains part of account creation and address correction. Existing-account address corrections include only fields that differ after normalization (and ZIP only if its original value differs). Equivalent spellings are not updates. CHOW creation still supplies the new account’s complete address.

The table uses `[placeholders]` for actual values. Matching facts and likely explanations are separate: an address match can support a correction without proving an acquisition, rebrand, or move.

## 2. Full scenario and card table

Evidence contains at most three normal bullets. Multiple candidates add a fourth ambiguity bullet. Financial evidence identifies the actual zero field rather than displaying an unresolved choice of wording.

| Scenario | If–then condition | Card title | Evidence | Why this action |
| --- | --- | --- | --- | --- |
| **Confident match** | If name and address match and the CRM parent is Bellhaven, then no name, address, or parent correction is needed. A ZIP difference does not change this result. | No name, address, or parent change needed for “[facility name]” | • Names match.<br>• Addresses match.<br>• CRM parent is Bellhaven. | The matching name, address, and parent suggest the same facility. These CRM fields already agree with Bellhaven's current listing. |
| **Name correction** | If the address matches, the CRM name differs or is missing, and the parent is Bellhaven, then suggest using the website name. | Change CRM facility name from “[CRM name]” to “[website name]” | • Addresses match.<br>• Names differ or CRM name is missing.<br>• CRM parent is Bellhaven. | This is likely a name change or an outdated CRM name. The matching address connects the records, and the account already belongs to Bellhaven. The website supplies the current listed name. |
| **Parent correction** | If name and address match, the parent differs or is missing, and lifetime revenue or outstanding AR is zero, then suggest changing the existing account's parent to Bellhaven. | Change parent of “[facility name]” from “[current parent]” to Bellhaven | • Names and addresses match.<br>• [CRM parent is missing / CRM parent differs from Bellhaven], based on the saved parent account ID.<br>• [Lifetime revenue is zero / Outstanding AR is zero / Both are zero]. | This is likely an ownership change or an outdated parent assignment. The name and address match a facility listed by Bellhaven. Because [lifetime revenue is zero / outstanding AR is zero / both are zero], the CHOW requirement to create a separate account does not apply. |
| **Name and parent correction** | If the address matches, the name and parent need correcting, and lifetime revenue or outstanding AR is zero, then suggest updating both on the existing account. | Rename “[CRM name]” to “[website name]” and change its parent to Bellhaven | • Addresses match, but names differ or CRM name is missing.<br>• [CRM parent is missing / CRM parent differs from Bellhaven], based on the saved parent account ID.<br>• [Lifetime revenue is zero / Outstanding AR is zero / Both are zero]. | This is likely an ownership change with a rebrand. The address matches, but the facility name needs updating. [CRM parent is missing / CRM parent differs from Bellhaven]. Because [lifetime revenue is zero / outstanding AR is zero / both are zero], no separate CHOW account is required; the existing record can carry the current name and parent. |
| **Address correction** | If no CRM address matches, a CRM name matches, and its parent is Bellhaven, then suggest replacing its address with the website address. | Change CRM address for “[facility name]” to “[website address]” | • Names match.<br>• Addresses differ; no CRM address matches.<br>• CRM parent is Bellhaven. | The matching name and parent suggest the same facility, with a possible move or an outdated CRM address. The website supplies its current listed address. |
| **Address and parent correction** | If no CRM address matches, a CRM name matches, its parent differs or is missing, and lifetime revenue or outstanding AR is zero, then suggest updating its address and parent. | Change the address of “[facility name]” to “[website address]” and its parent to Bellhaven | • Names match, but addresses differ.<br>• [CRM parent is missing / CRM parent differs from Bellhaven], based on the saved parent account ID.<br>• [Lifetime revenue is zero / Outstanding AR is zero / Both are zero]. | The matching name suggests the same facility, but its address and ownership details may be outdated. If these records represent the same facility, the website supplies its current address and Bellhaven ownership. Because [lifetime revenue is zero / outstanding AR is zero / both are zero], the existing account can be updated. |
| **CHOW replacement after an address match** | If the address matches, the account needs a parent correction, and lifetime revenue and outstanding AR are both greater than zero, then suggest creating a replacement account under Bellhaven and linking the old account to it. Names may match or differ. | Change of ownership with financial data and AR for “[website name]” | • Addresses match; names [match / differ / are missing in CRM].<br>• [CRM parent is missing / CRM parent differs from Bellhaven], based on the saved parent account ID.<br>• Lifetime revenue and outstanding AR are both greater than zero. | This is likely an ownership change or an outdated parent assignment. The existing account has both revenue history and unpaid receivables, so billing requires it to be preserved. A new account represents the facility under Bellhaven, while the link connects it to the old billing record. |
| **CHOW replacement after a name-only match** | If no CRM address matches, a CRM name matches, its parent needs correcting, and lifetime revenue and outstanding AR are both greater than zero, then suggest creating a replacement with the website details and linking the old account to it. | Change of ownership with financial data and AR for “[website name]” | • Names match, but addresses differ.<br>• [CRM parent is missing / CRM parent differs from Bellhaven], based on the saved parent account ID.<br>• Lifetime revenue and outstanding AR are both greater than zero. | The matching name suggests a facility whose address and ownership details may have changed. If the identity is confirmed, its revenue history and unpaid receivables require preserving the old account. A separate account can hold the website address and Bellhaven parent without altering the old billing record. |
| **Create account** | If the website record is complete and neither its name nor its address matches any eligible CRM account, then suggest creating an account under Bellhaven. No prior reviewer confirmation is required to generate this suggestion. | Create a CRM account for “[website name]” under Bellhaven | • No CRM name matches.<br>• No CRM address matches.<br>• Required website details are complete. | This facility appears to be missing from the CRM because neither its name nor its address matches an existing account. Creating it would capture a facility listed under Bellhaven. The reviewer should still check for an existing record with different details before approving creation. |
| **Absent from website** | If an eligible Bellhaven CRM facility has no corresponding website record after a complete scrape and is not involved in an unresolved match, then suggest marking it Inactive and adding an explanatory note. | Mark “[CRM name]” Inactive — not found on Bellhaven's website | • No match found on the website by either name or full address (Street + City + State).<br>• CRM parent of the record is Bellhaven. | This account may no longer belong in Bellhaven's active facility list because it is absent from the completed website scrape. Making it Inactive preserves its history while reflecting that absence. The website alone does not establish whether it closed or changed ownership. |

The name-only address-and-parent correction is agreed, including its CHOW variant.

## 3. Rules shared by all cards

**Same-name, same-address duplicates**

- If a website facility matches several CRM accounts with the same normalized name and complete address, save one `duplicate_resolution` proposal for their sorted account IDs. Parent and financial differences do not prevent the reviewer from considering this group.
- Card title: **Resolve Possible Duplicate - “[website name]”**.
- Evidence: “Website facility matches multiple CRM records”; “These CRM records have the same normalized name and address”; “These are separate CRM account IDs.”
- Why this action: “These records may be duplicate copies of the same facility. Choosing a survivor preserves one current record while retaining the other records as Inactive copies linked to it. Financial values and update dates help inform the choice; the reviewer makes the final decision.”
- In the same matching run, prepare each account's correction or no-change result using the normal rules. Link each result to the duplicate-resolution proposal through `depends_on_proposal_id`.
- The UI shows the duplicate-resolution card first. The linked results already exist in SQLite but stay hidden in the UI. The reviewer chooses the survivor, chooses a facility phone if the normalized numbers differ, reviews all retained contacts, and adds a note; choosing on screen does not apply changes.
- Contacts already on the survivor retain their IDs. Every contact on a loser is copied to the survivor with a new ID and its original name, role, email, phone and active status. Active originals become inactive after their copies are verified; already inactive originals remain inactive. Different people or roles are never merged. Contact reassignment through PATCH is unverified, so the implementation uses the tested create/deactivate operations.
- On submission, the backend preserves contacts first, applies the selected phone, then marks losers Inactive, sets their `duplicate_of_account`, adds their CRM notes, and dismisses their pending corrections with “Account resolved as a duplicate.” Local changes/history commit atomically. Production calls verify individually and pause on failure without remote rollback; success history and dependency release wait for the complete plan.
- Only after that succeeds does the UI reveal the survivor's remaining result for separate approval. Its contact operations are refreshed against the consolidated contacts, and any phone change already resolved by the reviewer is removed. Matching does not run again. Remaining matching and financial assumptions are still checked before applying corrections.
- An explicit “Keep both/all” decision leaves the accounts active and releases their prepared results. Rejecting or deferring the question does not resolve it and does not release corrections. Record the reason; do not ask the same group question again on every run.
- Later runs skip linked losers. Financial history and timestamps are evidence for the human; this script does not choose or deactivate a survivor/loser automatically.

**Same address, different names, and other ambiguity**

- Candidates with different normalized names do not enter the same duplicate group just because they share an address. Apply the normal rules separately and show the individual action cards immediately.
- If several website facilities share an address, apply the rules to each website–CRM candidate pair.
- Keep the normal evidence bullets and add a **fourth bullet**: “3 CRM candidates share this address,” “3 CRM candidates share this name,” or “2 website facilities share this address,” using actual counts. If both sides have several candidates, state both counts in that bullet.
- Append to the explanation: “Multiple records share these matching details, so this interpretation depends on confirming that this is the corresponding account.” They could be separate facilities, former operators, or duplicates.
- Do not describe these matches as unique. A matching website name is stronger evidence and remains visible in the normal evidence bullets.
- Approval must prevent incompatible changes to the same CRM account. Several candidate cards do not mean all their changes should be approved.

**CHOW: preserve the old account and use the current one next time**

The financial rule applies whenever a parent change is being considered:

| Revenue history | Outstanding AR | Result |
| --- | --- | --- |
| Greater than zero | Greater than zero | Suggest a new account under Bellhaven and a link from the old account. |
| Zero | Any valid nonnegative amount | Suggest a parent change on the existing account. |
| Any valid nonnegative amount | Zero | Suggest a parent change on the existing account. |

For an approved CHOW, create the new account using the correct facility details, then set the old account's `chow_current_account` to the new account's ID. **Every other old-account field stays unchanged**, including name, parent, address, status, financial values, and note. Creating the replacement and linking the old account must succeed together as one local transaction.

On later runs, if A points to B, match against B, not A. If B points to C, match against C. Old accounts remain in the CRM for history but are excluded from matching and absence proposals. The same exclusion applies to linked duplicate losers. Broken or circular links are reported as run issues instead of guessing which account is current. An existing CHOW link alone does not produce a card.


**Incomplete data and scope**


- Use the selected run’s completed scrape and normalized batch. Never fall back to an older scrape.
- Skip website records missing name, street, city, or state and report them in Runs. They do not get proposal cards. The script withholds absence proposals for that run because the missing details may conceal a corresponding CRM account.
- The current dataset has numeric lifetime revenue and outstanding AR values on every account. If future data is missing or invalid and the CHOW decision cannot be made, do not suggest parent change.
- Absence proposals preserve the account and parent; they change status and add an explanatory note. An already Inactive account does not need another suggestion merely to set it Inactive.
- These rules do not define corrections for care offerings, phone numbers, or other additional fields. Existing duplicate links identify losing accounts to exclude from matching and absence proposals.

**Decisions, repeat runs, and demo reset**

- The matching script writes proposals and their run associations. It does not apply CRM changes. Approval/rejection and application are separate steps.
- A correction proposal key identifies the target, action, and proposed values. A duplicate-resolution key identifies the sorted member account IDs. Run IDs, scrape timestamps, and supporting evidence are excluded, so a later run finding the same change recognizes it.
- Reuse an unchanged pending proposal. Keep an already decided change out of the queue, including rejected changes, even if supporting evidence changes. A different proposed change can produce a new proposal.
- Save decisions and local application history. A CHOW approval must not be applied twice.
- Demo reset restores `demo.sqlite` from the immutable `start.sqlite` baseline and recreates empty workflow tables. Demo runs, proposals, decisions, and application history are cleared so the demonstration can start again. `start.sqlite` and `production.sqlite` remain untouched.
- Demo and production use the same local proposal logic. Production API synchronization remains a separate, deferred step.

Older pending address cards containing redundant writes are superseded by minimal corrections on the next run. Submitted decisions are preserved and reused even when their old operation list included unchanged address fields.


## Additional website updates after matching

The identity, ownership, absence and duplicate rules above are unchanged. After matching,
compare the website facility phone, full care-offerings set and Administrator contact.
Include genuinely different or missing values in the proposal; formatting-only differences
and missing website values cause no update. Perfect name/address/parent matches still need
human review when these additional changes exist. Automatic completion requires no account
or contact operations. See [MATCHING_SCRIPT.md](MATCHING_SCRIPT.md#additional-website-updates)
for contact reuse/replacement, ambiguous choices, CHOW targets and repeat-decision handling.

## Proposal wording

`proposal_copy.py` supplies complete matching-evidence sentences and the
“Change of ownership with financial data and AR” title for both CHOW variants.
`match_records.py` saves this wording in `proposals.title` and
`proposals.supporting_evidence`; the UI displays the saved matching prose and CHOW title.
The evidence remains an array of sentences for compatibility, rendered as a checklist
under **Matching logic**. Parent evidence says **CRM parent is missing** when the saved
parent account ID is empty, or **CRM parent differs from Bellhaven** when a different
parent account ID is present. A missing display name does not mean the parent
relationship is missing. Legacy evidence is converted using the saved CRM snapshot. The saved explanation appears under **Why this action**.

To refresh existing proposal wording without rerunning matching or applying CRM
changes, run `python3 refresh_proposal_copy.py --database both`. This backs up each
changed database, updates proposal copy only, and is safe to repeat.
