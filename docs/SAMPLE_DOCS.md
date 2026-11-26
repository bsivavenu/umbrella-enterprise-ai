# Sample documents

Fake company documents for trying the app, in the [`sample_docs/`](../sample_docs) folder. In the
**Documents** tab you can select all of them at once (up to 10 files per upload; PDF, DOCX, TXT and
MD all work), wait for each to show `ready`, then ask the questions below.

This guide lives here, not in `sample_docs/`, so that selecting the whole folder does not upload it
into your knowledge base.

| File | Type | Ask | You should get |
|---|---|---|---|
| `leave_policy.txt` | text | How many vacation days do I get? | 24 paid days a year |
| `onboarding.md` | Markdown | What happens on my first day? | Laptop and badge pickup, buddy, security training |
| `it_security_rules.txt` | text | How long must a password be? | 14 characters |
| `holiday_policy.txt` | text | How many public holidays do we get? | 10, plus 1 floating holiday |
| `expense_policy.md` | Markdown | When does an expense need manager approval? | Any single expense over $250 |
| `remote_work_policy.docx` | Word | How many days can I work from home? | Up to 3 days a week, Tuesday is the office day |
| `travel_policy.pdf` | PDF | What is the hotel limit? | $180 per night |

Questions that span two files work too, for example: "What is the leave policy and the holiday
policy?" should cite both documents.

The facts are invented, so a correct answer proves the file was uploaded to S3, read, embedded,
stored in Pinecone and found again. A question about something in none of the files, such as
"What is the CEO's salary?", should say it cannot find supporting evidence.
