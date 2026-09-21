# Bellhaven normalization rules

These are the rules currently implemented in `normalize_data.py`.

## 1. Purpose

Create consistent versions of facility names and addresses so CRM records and website records can be compared later.

- Apply the same rules to both sources.
- Preserve original CRM values beside comparison values; link website comparison values to their saved source snapshot.
- Write only normalized comparison tables and normalization tracking in the working database. Never modify source CRM records, website snapshots, or `start.sqlite`.
- Use explicit dictionaries and positional rules, with no trained model or external service.
- Do not match facilities, merge accounts, or decide which record is correct.

**An identical normalized address is evidence for a possible match. It does not prove that two records represent the same facility.**

## 2. Common text cleanup

Apply these steps to facility names, streets, cities, and states:

1. Keep a missing value as missing (`None` in Python).
2. Convert any other value to text and lowercase it.
3. Split the text at whitespace, including spaces, tabs, and line breaks.
4. Join the resulting words with one ordinary space. This also removes leading and trailing whitespace.
5. If nothing remains, return a missing value.

Example: `  MAIN   Street  ` becomes `main street` before street-specific rules run.

### What counts as a word?

A word is one whitespace-separated element. Replacements require an exact match to the entire element.

- `Willow`, `Westchester`, and `Astoria` are never changed internally.
- Punctuation and accents are preserved, except standalone name separators described below.
- `street.` is different from `street`; `rehab,` is different from `rehab`.
- Literal text such as `N/A` is lowercased, not converted to a missing value.

## 3. Facility names

After common cleanup, remove standalone `-`, `–`, and `—` separators and a leading `the`. Keep hyphens inside words and any `the` elsewhere in the name. Then scan the words from left to right. First replace the adjacent two-word phrase `health care`; otherwise apply the single-word dictionary.

| Exact input | Replacement |
| --- | --- |
| `health care` | `healthcare` |
| `centre` | `center` |
| `rehab` | `rehabilitation` |
| `&` | `and` |
| `at` | `of` |

All other words and punctuation remain. In particular, retain brands, `nursing`, `care`, `manor`, and `campus`. These rules change comparison values only, never the original names. Whole-word `at` and `of` are equivalent; both use `of` in comparison values.

| Original | Normalized |
| --- | --- |
| `Bellhaven Health Care Centre` | `bellhaven healthcare center` |
| `Bellhaven Rehab & Nursing` | `bellhaven rehabilitation and nursing` |
| `The Arbors at Bellhaven - Dayton` | `arbors of bellhaven dayton` |

An older pending name correction is no longer actionable if its names are equal under these rules. Its saved evidence and submitted decisions are preserved. A new run generates any remaining corrections.

`Bellhaven at Sycamore Ridge` and `Bellhaven of Sycamore Ridge` now compare as the same name. Words such as `Atlas` and hyphenated words remain unchanged. This rule applies only to facility names, not street addresses.

## 4. Street addresses

Apply common cleanup first, then the following steps in order.

### Step A — Locate the street suffix

A suffix is a street type, such as `road` or `street`.

1. Inspect the last word.
2. If it is a direction from the direction table below, or one of its abbreviations, inspect the preceding word instead.
3. The inspected word must be one of these exact values:

   `road`, `rd`, `street`, `st`, `avenue`, `ave`, `boulevard`, `blvd`, `drive`, `dr`, `lane`, `ln`, `pike`, `pk`.

4. If there is no word at that position, or it is not in this list, stop street-specific processing. Keep only the common text cleanup.

The script does not search other positions for a suffix.

### Step B — Normalize the suffix

At the position identified in Step A, apply this dictionary:

| Exact input | Replacement |
| --- | --- |
| `road` | `rd` |
| `street` | `st` |
| `avenue` | `ave` |
| `boulevard` | `blvd` |
| `drive` | `dr` |
| `lane` | `ln` |
| `pk` | `pike` |

Suffixes already in the replacement column remain unchanged. `pk` becomes `pike` to reconcile the variation observed between our datasets. This rule applies to every street at the suffix position identified in Step A, not only Wilmington.

This step does not require a numeric first word.

### Step C — Abbreviate directions

| Exact input | Replacement |
| --- | --- |
| `north` | `n` |
| `south` | `s` |
| `east` | `e` |
| `west` | `w` |
| `northeast` | `ne` |
| `northwest` | `nw` |
| `southeast` | `se` |
| `southwest` | `sw` |

**First-word requirement:** direction replacements run only if the first word consists entirely of the digits `0` through `9`. Otherwise, keep directions unchanged. Suffix replacements from Step B still apply.

For addresses that meet this requirement:

- **Final direction:** abbreviate the last word if it is a direction immediately after the recognized suffix, with at least one word between the number and that suffix.
- **Initial direction:** abbreviate the word immediately after the number if it is a full direction word, with at least one additional word between it and the recognized suffix. A final direction does not affect this check.
- Leave directions in all other positions unchanged. Already abbreviated directions remain unchanged.

These are checks of word positions, not an interpretation of the official street name.

### Street examples

| Original | Normalized | Reason |
| --- | --- | --- |
| `123 West Main Street` | `123 w main st` | Initial direction and final suffix. |
| `123 West Street` | `123 west st` | No separate word between `west` and the suffix. |
| `123 Main Street West` | `123 main st w` | Direction immediately after the suffix. |
| `123 Main West Street` | `123 main west st` | `west` is neither the initial nor final direction. |
| `123 North Main Street West` | `123 n main st w` | Both direction rules apply. |
| `123 Westchester Drive` | `123 westchester dr` | No replacement inside `westchester`. |
| `45 St Lawrence Dr` | `45 st lawrence dr` | Internal `st` remains part of the street name. |
| `3313 Wilmington Pk` | `3313 wilmington pike` | Treat `pk` and `pike` as equivalent suffixes. |
| `123A West Main Street` | `123a west main st` | First word contains a letter; only the suffix changes. |
| `123-125 West Main Street` | `123-125 west main st` | First word contains a hyphen; only the suffix changes. |
| `123 West Main Street Apt 4` | `123 west main street apt 4` | No recognized suffix at the inspected position. |
| `123 Main Street.` | `123 main street.` | Punctuation prevents an exact suffix match. |
| `PO Box 517` | `po box 517` | Common cleanup only. |

## 5. Cities, states, and ZIP codes

| Field | Treatment |
| --- | --- |
| City | Common text cleanup only. No spelling corrections or city aliases. |
| State | Common text cleanup only. Full state names are not converted to abbreviations. |
| ZIP | Preserved exactly as read from the database. No trimming, padding, formatting, or corrections. |

For example, `Ohio` becomes `ohio`, while `OH` becomes `oh`. They remain different.

## 6. Output fields and missing addresses

Each saved comparison row contains:

| Added field | Meaning |
| --- | --- |
| `normalized_name` | Facility name after the name rules. |
| `normalized_street` | Street after the street rules. |
| `normalized_city` | City after common cleanup. |
| `normalized_state` | State after common cleanup. |
| `address_fields_present` | `True` only when normalized street, city, and state are all non-missing. |

Missing normalized values are stored as SQL `NULL`. The original ZIP field is retained in the CRM copy or linked website snapshot; there is no normalized ZIP field. No CSV files are written.

**`address_fields_present` checks presence only.** It does not validate an address, recognize unsupported formats, or measure confidence.

For the later matching stage, the agreed address comparison uses **street + city + state together**, with all three present. ZIP differences are reviewed separately. The normalization script does not implement that matching stage.

## 7. Limits of these rules

- They do not cover every US address format.
- They do not verify that an address exists.
- They do not correct typos, distinguish a billing address from a physical address, or detect relocations.
- They do not split or remove apartment, unit, or building information.
- They do not automatically flag every unsupported or ambiguous address. Review the original and normalized columns together.
- Shared addresses, different brands, campus entities, and ownership changes require separate matching decisions.

The same input produces the same output with this implementation. Any additional equivalence must be added explicitly to the rules.

## 8. Database input and output

Run after a successful scrape, using the run ID printed by the scraper:

```sh
python3 normalize_data.py --database demo --run-id YOUR_RUN_ID
```

The script reads that run's `website_snapshot_id` and current `crm_accounts`. It requires `scraping_status = complete`; it never substitutes an older successful scrape. Demo and production use the same command and schema, with `--database production` selecting the other working database.

It saves `normalized_crm_accounts` and `normalized_website_locations` in one transaction, along with `normalization_status` and record counts. Original CRM fields, including finances and account links, are copied unchanged. Original website fields remain in `website_snapshots`.

A completed batch is reused without changing its records, even if the current CRM has since changed. Start a new run to compare new data. Partial or already-used batches are never overwritten. Processing failures roll back both output tables and are recorded in the run's error log when the database remains writable; retries preserve scraper warnings.

The script updates its normalization stage only. Existing scrape results stay intact; a future pipeline controls the overall pipeline status and completion time. A short normalization executes in one transaction, so other readers see the final stage result when it commits.

An empty CRM cannot be normalized. After demo reset, first complete a new scrape or explicitly prepare a saved-snapshot replay run; the reset baseline contains no run history.
