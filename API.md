# Bellhaven CRM Sandbox API

- **API version:** 0.1.0
- **OpenAPI version:** 3.1
- **OpenAPI document:** `/api/openapi.json`
- **Response media type:** `application/json`
- **Documentation:** [Live Swagger UI](https://analyst-assessment-production.up.railway.app/api/docs)
- **Server origin:** `https://analyst-assessment-production.up.railway.app`
- **POST/PATCH documentation last checked:** 2026-09-21

This document separates the published Swagger reference from observed API behavior. The site lists two POST operations and two PATCH operations. Each was inspected through its expanded documentation, response Example Value and Schema tabs, and the input controls revealed by Try it out. That original documentation-only inspection made no CRM calls. The later sections record user-executed account tests and explicitly authorized assistant-executed contact tests, all using the designated test records.

**Request versus response:** a request body contains the data we send to create or update a record. The Responses section describes what the server sends back. Its `application/json` selector controls the `Accept` header; it does not specify a request body or its `Content-Type`. The displayed success example `"string"` is not an account/contact payload: the inspected success Schema tabs say `any` and define no record fields.

## Accounts

### GET `/api/v1/accounts`

List accounts.

#### Parameters

| Name | Location | Type | Required | Default |
|---|---|---|---|---|
| `q` | Query | String | No | Empty string |
| `city` | Query | String | No | Empty string |
| `state` | Query | String | No | Empty string |
| `zip` | Query | String | No | Empty string |
| `street` | Query | String | No | Empty string |
| `parent_id` | Query | String | No | Empty string |
| `page` | Query | Integer | No | `1` |
| `page_size` | Query | Integer | No | `50` |

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

### POST `/api/v1/accounts`

Create an account.

**Source:** [Create Account](https://analyst-assessment-production.up.railway.app/api/docs#/accounts/create_account_api_v1_accounts_post).

**Parameters:** The live page says “No parameters.”

**Request body:** No Request body section, JSON example, field schema, required-field list, or request media type is shown. Enabling Try it out reveals Execute and Cancel buttons, but no account fields or body editor. This documents an omission; it does not establish that an empty request is valid.

#### Responses

| Status | Description | Media type | Displayed example | Schema tab |
|---|---|---|---|---|
| `201` | Successful Response | `application/json` | `"string"` | `any`; no fields defined |

Only `201` is listed for this operation. Swagger does not specify error responses, response links, or the structure containing a newly created account's ID. The later [observed account tests](#account-creation-observed-in-user-executed-requests) supply verified request and response examples, including an actual `422` response.

### GET `/api/v1/accounts/{account_id}`

Get an account.

#### Parameters

| Name | Location | Type | Required |
|---|---|---|---|
| `account_id` | Path | String | Yes |

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

### PATCH `/api/v1/accounts/{account_id}`

Update an account.

**Source:** [Update Account](https://analyst-assessment-production.up.railway.app/api/docs#/accounts/update_account_api_v1_accounts__account_id__patch).

#### Parameters

| Name | Location | Type | Required |
|---|---|---|---|
| `account_id` | Path | String | Yes |

**Request body:** No Request body section, JSON example, writable-field schema, or request media type is shown on the live page. Try it out enables only the `account_id` path input; it does not reveal a body editor or account fields.

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

The `200` response media type is `application/json`; its Schema tab says `any` and defines no account fields. Both responses show “No links.” See [observed account PATCH behavior](#account-patch-and-derived-parent-name) for the tested payload and actual response.

## Contacts

### GET `/api/v1/contacts`

List contacts.

#### Parameters

| Name | Location | Type | Required | Default |
|---|---|---|---|---|
| `account_id` | Query | String | No | Empty string |
| `q` | Query | String | No | Empty string |
| `page` | Query | Integer | No | `1` |
| `page_size` | Query | Integer | No | `50` |

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

### POST `/api/v1/contacts`

Create a contact.

**Source:** [Create Contact](https://analyst-assessment-production.up.railway.app/api/docs#/contacts/create_contact_api_v1_contacts_post).

**Parameters:** The live page says “No parameters.”

**Request body:** No Request body section, JSON example, field schema, required-field list, or request media type is shown. Enabling Try it out reveals Execute and Cancel buttons, but no contact fields or body editor. This does not establish whether fields such as a linked account ID are required or accepted.

#### Responses

| Status | Description | Media type | Displayed example | Schema tab |
|---|---|---|---|---|
| `201` | Successful Response | `application/json` | `"string"` | `any`; no fields defined |

Only `201` is listed for this operation. Swagger does not specify error responses, response links, or the structure containing a newly created contact's ID. See [observed contact tests](#contact-creation-and-updates-observed-in-authorized-tests) for a verified creation payload and returned `contact_id`.

### GET `/api/v1/contacts/{contact_id}`

Get a contact.

#### Parameters

| Name | Location | Type | Required |
|---|---|---|---|
| `contact_id` | Path | String | Yes |

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

### PATCH `/api/v1/contacts/{contact_id}`

Update a contact.

**Source:** [Update Contact](https://analyst-assessment-production.up.railway.app/api/docs#/contacts/update_contact_api_v1_contacts__contact_id__patch).

#### Parameters

| Name | Location | Type | Required |
|---|---|---|---|
| `contact_id` | Path | String | Yes |

**Request body:** No Request body section, JSON example, writable-field schema, or request media type is shown on the live page. Try it out enables only the `contact_id` path input; it does not reveal a body editor or contact fields.

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |
| `422` | Validation Error | See [validation error example](#validation-error-example) |

The `200` response media type is `application/json`; its Schema tab says `any` and defines no contact fields. Both responses show “No links.” See [observed contact tests](#contact-creation-and-updates-observed-in-authorized-tests) for verified role and active-status updates.

## Meta

### GET `/api/v1/me`

Me.

**Parameters:** None listed.

#### Responses

| Status | Description | Example |
|---|---|---|
| `200` | Successful Response | `"string"` |

## Schemas

### HTTPValidationError

Type: Object.

| Field | Type |
|---|---|
| `detail` | Array of `ValidationError` objects |

### ValidationError

Type: Object.

| Field | Type |
|---|---|
| `loc` | Array whose items are strings or integers |
| `msg` | String |
| `type` | String |

### Validation error example

The listed `422` responses show this example:

```json
{
  "detail": [
    {
      "loc": [
        "string",
        0
      ],
      "msg": "string",
      "type": "string"
    }
  ]
}
```

## Details still absent from the live write documentation

- POST body structure, required fields, defaults, and request media type for both accounts and contacts.
- PATCH body structure, writable fields, and the meaning of omitted or null values.
- Whether IDs, timestamps, parent display names, and candidate flags are supplied by the caller or generated by the server.
- Actual success response structures, including the location of newly created record IDs; `any` does not define these.
- Authentication instructions in this Swagger interface.
- Transaction/batch guarantees, conditional updates, and duplicate-request/idempotency behavior.

These are gaps in the published documentation, not claims that the API lacks those capabilities. Existing SQLite column names and downloaded CRM fields are not proof of accepted write payloads. The subsequent observed tests below resolve the specific inputs and outcomes they cover. No payload examples have been invented.

The documented `422` JSON describes an error response, not a request body. It is listed for the PATCH operations; neither POST section lists it.

## Account creation observed in user-executed requests

**Evidence:** terminal responses supplied by the user on 2026-09-21. These observations supplement the incomplete Swagger reference; the assistant did not execute the requests. Credentials are intentionally omitted.

The user's authenticated request used `Authorization: Bearer <token>` and `Content-Type: application/json`.

### Empty creation payload

`POST /api/v1/accounts` with `{}` returned HTTP `422`:

```json
{"detail":"name is required"}
```

This observed error has a string-valued `detail`, unlike the array-valued validation-error example in Swagger. Error handling must accommodate both shapes.

### Successful minimal creation payload

`POST /api/v1/accounts` with the following body returned HTTP `201`:

```json
{"name":"test"}
```

The actual response was:

```json
{"account_id":"001BEA469CFB6E36C9","message":"created"}
```

For this request, `name` alone was sufficient. The new ID is returned directly in `account_id`, not inside a `data` wrapper. This created a real test record; it was not a dry run.

### Saved record and observed defaults

The user's subsequent `GET /api/v1/accounts/001BEA469CFB6E36C9` returned HTTP `200` and the account object directly:

```json
{
  "account_id": "001BEA469CFB6E36C9",
  "name": "test",
  "parent_id": "",
  "parent_name": "",
  "billing_street": "",
  "billing_city": "",
  "billing_state": "",
  "billing_zip": "",
  "care_type": "",
  "status": "Active",
  "phone": "",
  "lifetime_revenue": 0,
  "outstanding_ar": 0,
  "chow_current_account": "",
  "duplicate_of_account": "",
  "note": "",
  "created_by_candidate": true,
  "updated_at": "2026-09-21 08:53:55Z"
}
```

The server supplied the account ID and timestamp, set `created_by_candidate` to JSON boolean `true`, defaulted status to `Active` and financial values to numeric zero, and stored the other omitted fields as empty strings. These are observed defaults for this minimal creation, not a full specification of field validation or behavior when values are explicitly supplied.

### Creation with populated account fields

The user supplied a second POST result at `2026-09-21 09:09:32 GMT` and a follow-up GET at `09:10:05 GMT`. This creation body returned HTTP `201`:

```json
{
  "name": "test 2",
  "parent_id": "0015QAPLGS3FVYEEEM",
  "parent_name": "Bellhaven Senior Living (Parent Account)",
  "billing_street": "1 API Test Lane",
  "billing_city": "Test City",
  "billing_state": "OH",
  "billing_zip": "45202",
  "care_type": "Assisted Living; Memory Support",
  "status": "Inactive",
  "phone": "(202) 555-0147",
  "lifetime_revenue": 0,
  "outstanding_ar": 0,
  "chow_current_account": "",
  "duplicate_of_account": "",
  "note": "API TEST RECORD — not a real facility."
}
```

Creation response:

```json
{"account_id":"00138DEF94BA9F9EFA","message":"created"}
```

`GET /api/v1/accounts/00138DEF94BA9F9EFA` returned HTTP `200`. Every field above matched its submitted value. The returned object also contained:

```json
{
  "account_id": "00138DEF94BA9F9EFA",
  "created_by_candidate": true,
  "updated_at": "2026-09-21 09:09:32Z"
}
```

This verifies creation with the populated name, parent ID, address, care type, status, phone, and note shown above. Both parent fields were sent together, so the result does not establish whether `parent_name` was accepted directly or derived from `parent_id`. Zero financial amounts and empty relationship links match the previously observed defaults; their presence in this request does not prove that nondefault values are writable. This test does not establish PATCH behavior.

### Account PATCH and derived parent name

The user supplied a PATCH response at `2026-09-21 09:11:07 GMT` and follow-up GET at `09:11:20 GMT`. The request was `PATCH /api/v1/accounts/001BEA469CFB6E36C9` with:

```json
{
  "name": "test - updated",
  "parent_id": "0015QAPLGS3FVYEEEM",
  "billing_street": "2 API Test Lane",
  "billing_city": "Test City",
  "billing_state": "OH",
  "billing_zip": "45202",
  "care_type": "Assisted Living; Memory Support",
  "phone": "(202) 555-0148",
  "status": "Inactive",
  "note": "API TEST RECORD — updated for verification."
}
```

The API returned HTTP `200`:

```json
{
  "account_id": "001BEA469CFB6E36C9",
  "message": "updated",
  "fields": [
    "billing_city", "billing_state", "billing_street", "billing_zip",
    "care_type", "name", "note", "parent_id", "parent_name", "phone", "status"
  ]
}
```

The subsequent GET returned HTTP `200` and confirmed every requested value. Although the request omitted `parent_name`, the API changed it from an empty string to `Bellhaven Senior Living (Parent Account)` and included it in the `fields` response. For this valid parent ID, account PATCH derives the parent name; a separate `parent_name` write was unnecessary.

The omitted financial amounts remained zero, the two relationship links remained empty, and `created_by_candidate` remained `true`. The server advanced `updated_at` to `2026-09-21 09:11:07Z` without that field being submitted. This confirms preservation of those values in this request, not a universal guarantee for all fields and PATCH operations. The observed `fields` array did not include the automatically changed timestamp.

The successful account PATCH response contains the account ID, a message, and a list of fields rather than the full record. Read back the record to verify final values.

### Duplicate link and preservation of populated fields

The user supplied a PATCH response at `2026-09-21 09:12:13 GMT` and follow-up GET at `09:12:22 GMT`. The request to `/api/v1/accounts/001BEA469CFB6E36C9` was:

```json
{
  "status": "Inactive",
  "duplicate_of_account": "00138DEF94BA9F9EFA",
  "note": "API TEST RECORD — duplicate link test."
}
```

PATCH returned HTTP `200`:

```json
{
  "account_id": "001BEA469CFB6E36C9",
  "message": "updated",
  "fields": ["duplicate_of_account", "note", "status"]
}
```

The GET confirmed the nonempty duplicate link to the second test account, `Inactive` status, and the exact submitted note. Name, parent ID/name, all address fields, phone, care type, financial values, CHOW link, and candidate flag matched the preceding GET. Only the duplicate link, note, and server-generated `updated_at` changed; status was already `Inactive`. This validates a populated duplicate reference and preservation of the omitted populated account fields in this request. It does not independently test an Active-to-Inactive transition, which was observed in the earlier account PATCH.

The note replaced the previous note rather than appending to it. To implement the local `append_note` operation, the production request must carry the intended combined note after checking the current value; simply sending the new text would discard the old text. This test did not fetch the target account again to check for possible server-side effects on it.

### Clearing a duplicate link

At `2026-09-21 09:13:22 GMT`, the user's `PATCH /api/v1/accounts/001BEA469CFB6E36C9` with `{"duplicate_of_account":""}` returned HTTP `200`:

```json
{"account_id":"001BEA469CFB6E36C9","message":"updated","fields":["duplicate_of_account"]}
```

The follow-up GET at `09:13:26 GMT` confirmed `duplicate_of_account` was an empty string. All other returned fields matched the preceding record except `updated_at`, which advanced to `2026-09-21 09:13:22Z`. The account remained Inactive and retained its test note. This establishes that an empty string clears this duplicate link; it does not establish null handling or clearing other relationship fields.

### CHOW link and preservation of the old account

At `2026-09-21 09:14:02 GMT`, the user's `PATCH /api/v1/accounts/001BEA469CFB6E36C9` with `{"chow_current_account":"00138DEF94BA9F9EFA"}` returned HTTP `200`:

```json
{"account_id":"001BEA469CFB6E36C9","message":"updated","fields":["chow_current_account"]}
```

The follow-up GET at `09:14:12 GMT` confirmed the CHOW link. Name, parent ID/name, address, phone, care type, status, financial values, note, duplicate link, and candidate flag matched the preceding record. The server advanced `updated_at` to `2026-09-21 09:14:02Z`. Thus the tested link-only PATCH preserves the old account's business fields but changes server-managed timestamp metadata. Production read-back must preserve that actual server timestamp rather than requiring the local demo's unchanged CHOW timestamp.

This tests the link write to an already-created account, not an atomic create-and-link transaction. It does not establish rollback guarantees, CHOW-link clearing, or effects on the target account, which was not fetched again in this step.

## Contact creation and updates observed in authorized tests

**Evidence:** requests executed by the assistant on 2026-09-21 after the user explicitly authorized the three remaining contact checks. All writes targeted a newly created fictional contact on `test 2` (`00138DEF94BA9F9EFA`). No existing account or existing contact was modified. Authentication used `Authorization: Bearer <token>`; writes used `Content-Type: application/json`. Credentials are omitted.

Exactly seven requests were made sequentially: one read to avoid duplicate creation, then POST/GET, PATCH/GET, and PATCH/GET. There were no retries or cleanup writes.

| Time (UTC) | Request | HTTP status | Observed result |
|---|---|---|---|
| 09:16:22 | GET `/api/v1/contacts?account_id=00138DEF94BA9F9EFA` | 200 | No contacts existed on test 2: `{"data":[],"page":1,"page_size":50,"total":0}` |
| 09:16:34 | POST `/api/v1/contacts` | 201 | Created test contact `003D449B803EFC5F21` |
| 09:16:43 | GET `/api/v1/contacts/003D449B803EFC5F21` | 200 | Verified its account, name, Administrator title, active flag, and null email/phone |
| 09:16:53 | PATCH `/api/v1/contacts/003D449B803EFC5F21` | 200 | Set Former Administrator title and inactive flag |
| 09:17:00 | GET `/api/v1/contacts/003D449B803EFC5F21` | 200 | Verified deactivation, changed title, and preservation of other business fields |
| 09:17:10 | PATCH `/api/v1/contacts/003D449B803EFC5F21` | 200 | Restored Administrator title and active flag |
| 09:17:19 | GET `/api/v1/contacts/003D449B803EFC5F21` | 200 | Verified reactivation of the same contact and preservation of other business fields |

### Contact creation payload and response

Verified POST body:

```json
{
  "account_id": "00138DEF94BA9F9EFA",
  "name": "Test Administrator",
  "title": "Administrator",
  "is_active": true,
  "email": null,
  "phone": null
}
```

HTTP `201` response:

```json
{"contact_id":"003D449B803EFC5F21","message":"created"}
```

The following GET returned the contact object directly:

```json
{
  "contact_id": "003D449B803EFC5F21",
  "account_id": "00138DEF94BA9F9EFA",
  "name": "Test Administrator",
  "title": "Administrator",
  "email": null,
  "phone": null,
  "is_active": true,
  "created_by_candidate": true,
  "updated_at": "2026-09-21 09:16:34Z"
}
```

The API accepted this combination of fields and preserved explicit null email/phone. It generated the contact ID, candidate flag, and timestamp. This is a verified working payload, not a test of which individual fields are required. Because `is_active: true` was explicitly sent, its default when omitted remains untested.

### Deactivation and reactivation

Verified deactivation PATCH body:

```json
{"title":"Former Administrator","is_active":false}
```

HTTP `200` response:

```json
{"contact_id":"003D449B803EFC5F21","message":"updated","fields":["is_active","title"]}
```

The GET confirmed `title: "Former Administrator"`, `is_active: false`, and `updated_at: "2026-09-21 09:16:53Z"`. Contact ID, account ID, name, null email/phone, and candidate flag were unchanged.

Verified reactivation PATCH body:

```json
{"title":"Administrator","is_active":true}
```

The response was again HTTP `200` with the same `contact_id`, `message: "updated"`, and `fields: ["is_active", "title"]`. The final GET confirmed the original contact fields with `title: "Administrator"`, `is_active: true`, and `updated_at: "2026-09-21 09:17:10Z"`. No replacement contact was created.

Production payloads should serialize active flags as the tested JSON booleans, even though local SQLite stores them as integers. A newly created account's returned `account_id` can supply the contact association; a created contact's ID is returned directly in `contact_id`. Fetch the contact after writes to verify values, since PATCH returns a field list rather than a full record.

The Former Administrator title was a test value used to verify both writable fields. The existing application's former-administrator operation changes only `is_active`; the test does not authorize adding title changes to production decisions.

### Final test state

The new fictional contact `003D449B803EFC5F21` remains active with title Administrator on test 2. Both pre-existing test accounts were left untouched by this contact sequence. The last user-supplied account results show both test accounts Inactive, with the original test account pointing to test 2 through `chow_current_account` and its duplicate link empty. No test-record deletion or additional cleanup was performed.

## Verified integration scope and remaining limits

The observed tests cover the main operation types required by the reviewed examples: account creation with populated facility fields; ordinary account PATCH; parent-name derivation on PATCH; inactive status and notes; duplicate links; CHOW links preserving old business fields; and administrator contact creation, role changes, deactivation, and reactivation.

Unverified behaviors should not be assumed or require speculative production probes:

- Required/minimal contact fields beyond the working creation payload, other contact field updates, and reassignment to another account.
- Parent clearing/invalid-ID behavior and whether POST also derives the parent name when it is omitted. The verified account creation payload supplies both parent fields.
- CHOW-link clearing, if needed for recovery, and general null handling beyond the tested contact-creation email/phone values.
- Conditional writes, idempotency, multi-request transactions, and preservation guarantees beyond the specific tested cases.

The production executor must record request progress and returned IDs, verify saved values, and handle partial failures without assuming a group of API requests rolls back together. An uncertain POST outcome must be reconciled before repeating creation. These are implementation requirements; successful-request tests do not establish server guarantees.


## Implemented production submission (2026-09-21)

`production_api.py` converts the saved `approved_changes` operations into ordered requests:

| Local operation | Production request |
| --- | --- |
| `create` | POST `/api/v1/accounts`, excluding generated ID/flag/timestamp |
| `update` | PATCH `/api/v1/accounts/{account_id}` with only the approved values; parent name is derived from `parent_id` |
| `append_note` | PATCH of `note`, composed from the current note plus the approved addition |
| `create_contact` | POST `/api/v1/contacts`, resolving any newly created account ID and sending JSON boolean `is_active` |
| `update_contact` | PATCH `/api/v1/contacts/{contact_id}` with only the approved values |

Production mode loads complete paginated account/contact lists into a separate mirror. Preparing a preview performs reads and validation only; it stores an immutable plan and SHA-256 digest. Confirmation executes that stored plan. The only deferred values are account IDs returned by preceding verified creation calls; the preview shows those dependencies explicitly. Every write is followed by GET verification, and each returned ID/result is journalled in SQLite before subsequent writes. PATCH verification checks preserved business fields as well as requested changes, allowing the server-managed timestamp to advance.

`production_plans` and `production_requests` in working schema version 4 retain the preview and per-call progress across restarts. A process lock prevents simultaneous execution. Started/paused submissions block edits and new pipelines until reconciled. Partial execution does not create a successful decision-history entry; the entire plan must verify first. Preview decisions and proposal versions are checked again at confirmation, as is the full remote snapshot. This is a client-side freshness check, **not** a server conditional-update guarantee.

The HTTP client does not follow redirects or retry writes. Definite validation/authentication rejections may be retried explicitly; ambiguous outcomes stop execution. A lost POST response requires human-supplied new-record ID reconciliation and GET verification, and is never blindly repeated. A known successful write with failed read-back resumes with GET only. An uncertain PATCH is read back without automatic replay; if the approved result is absent or mismatched, manual reconciliation is required. There is no automatic rollback of remote changes. Only plans without potentially successful writes can be discarded.

Duplicate resolution uses the verified contact POST and active-status PATCH operations. It does **not** assume a contact's `account_id` is writable via PATCH: reassignment remains untested. Each losing contact is copied to the survivor, GET-verified, then its active original is deactivated. Names, roles, emails, phones and active status are preserved on the copies; generated IDs/flags/timestamps are new. Already inactive originals are unchanged. Only after contact preservation does the plan apply the chosen facility phone and deactivate/link duplicate accounts. `source_contact_id` and `duplicate_resolution` operation metadata remain local and are not sent in CRM bodies. Existing drafts created before this behavior must be reviewed again before preparing a new submission.

The local HTTP boundary requires a Production session, saved plan ID/digest, and explicit `confirm: true`. Test sessions cannot access production preview/execution. `BELLHAVEN_API_TOKEN` is loaded on the server and omitted from previews. All implementation tests use a fake CRM; no additional live API probes were made for this implementation.
