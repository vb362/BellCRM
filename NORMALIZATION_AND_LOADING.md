# Normalization versus loading data

The matching script compares scraped website locations with CRM accounts to propose corrections. It needs consistent names and addresses to find matches, plus financial fields and account links to decide which changes are appropriate.

**Normalization changes how a value is represented for comparison. Loading simply reads its existing value from the database.**

- **Names and addresses:** create normalized comparison values. For example, `123 Main Street` becomes `123 main st`. Preserve the original values alongside them.
- **Revenue, outstanding AR, and status:** read them unchanged. For example, revenue of `50,000` and outstanding AR of `2,000` help determine how an ownership change should be handled.
- **CHOW and duplicate links:** read them unchanged to check whether an account already points to a replacement or duplicate account.

The normalization script now loads all original CRM fields and saves them alongside the normalized comparison values in `normalized_crm_accounts`. The matching script can read them there; they do not need normalization. Website comparison values go into `normalized_website_locations`, linked to their original snapshot.

**Normalize names and addresses for comparison, and pass the other fields through unchanged. Saving comparison data does not modify the original CRM records.**
