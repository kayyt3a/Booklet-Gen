# FolioAI paid-beta plan

The paid beta is a controlled release to 10 to 20 adults who understand that
FolioAI is new. It validates payment, generation quality, printing, support,
and repeat use before public promotion.

## Entry conditions

Do not invite beta customers until all conditions are true:

- the release commit is deployed from the review branch or approved main
- clean-room mode is enabled and no unapproved vector store exists
- only the approved launch products appear in the customer menu
- the Render web service and background worker are both healthy
- Supabase database backups and the private `booklets` bucket are active
- Stripe test checkout and webhook replay pass without duplicate credits
- verification and reset emails reach a real inbox
- Privacy, Terms, Support, and Pricing contain real seller details
- the founder has approved `LAUNCH_OFFER.md`

## Founder acceptance run

Use a new disposable customer account.

1. Sign up and receive the verification email.
2. Verify the account and confirm exactly one welcome credit.
3. Generate Academic Accelerate Mathematics and download the PDF.
4. Generate Academic Accelerate English and download the PDF.
5. Generate independent literacy and numeracy practice for one eligible year.
6. Print at least one booklet on an ordinary home printer.
7. Confirm every answer, page reference, and student-name occurrence in the
   printed booklet.
8. Buy one credit in Stripe test mode.
9. Refresh the success page and replay the webhook. Confirm only one credit was
   granted.
10. Trigger one controlled failed job and confirm its reserved credit returns.
11. Restart the Render web service and download the existing booklet again.
12. Reset the password, export account data, and delete the disposable account.
13. Run `scripts/launch_readiness.py --stage beta --env-file .env` against the
    prepared settings.

## Beta customer tasks

Ask each tester to complete at least one real workflow:

- create a booklet for a student they know
- read the lesson and questions before giving it to the student
- print it or inspect every page at full size
- use at least part of the booklet with the student
- check the answer key after use
- report the time needed, confusing points, and whether they would pay again

## Feedback questions

1. What year level and subject did you choose?
2. Was it clear what you would receive before generation?
3. How long did generation feel?
4. Did the difficulty suit the student?
5. Did the lesson help the student start independently?
6. Did any question have a wrong, ambiguous, unsafe, or off-topic answer?
7. Did the PDF print cleanly without scaling or clipped content?
8. Which part was most useful?
9. Which part should be removed or changed?
10. Would you buy one booklet for A$6.90?
11. Would you buy ten booklet credits for A$36.00?
12. What would stop you from using FolioAI again?

Do not collect a child's surname, school, date of birth, diagnosis, assessment
result, or other sensitive information in the feedback form.

## Go or no-go measures

Public launch requires all of these across the latest beta round:

- at least 20 successfully generated booklets
- technical job success rate of at least 95 percent
- no confirmed cross-account access, duplicate credit, or lost-payment defects
- no confirmed restricted-source or copied-assessment content
- no unresolved critical or high incident
- at least 80 percent of testers describe the booklet as usable without a
  support intervention
- at least half of testers say they would probably buy at one offered price
- every materially wrong answer or broken layout has a regression check or a
  documented reason for holding launch

If any safety, privacy, payment, or cross-account defect appears, the beta does
not pass regardless of the percentages.

## Beta log columns

Maintain a private sheet with:

- tester id, not child name
- invitation date
- product and year level
- job id
- success or failure
- generation duration
- print tested
- quality issue category
- support action
- would buy single
- would buy term pack
- follow-up complete

Do not store secrets or full payment details in the beta log.
