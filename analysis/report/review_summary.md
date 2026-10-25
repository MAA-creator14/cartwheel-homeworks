# Review summary

## Sample: size and composition

My sample is 100 traces drawn from 98 sessions in the HW3 final run, which contains 324 traces across 250 sessions (`final-*`). Each trace is a single user turn. The review app showed the full session for context, but I only scored the turn that was sampled.   

I drew the sample in four batches:

- **batch1 (30 traces):** 15 chosen uniformly at random, plus one representative from each of 15 k-means clusters built on role, turn, tool counts, `ok:false` and text lengths.
- **batch2 (30 traces):** 10 chosen uniformly at random for each user role (shopper, merchant and support). I picked this dimension before I had seen the batch1 outcomes.
- **batch3 (25 traces):** a targeted search with one trace per candidate mode. I filtered on tool inputs and results, not on the reply, so I could find both positives and close negatives.
- **batch4 (15 traces):** chosen uniformly at random from the remaining pool once the draft taxonomy existed, as a saturation check.

All batches used seed 20261021, and the manifest is in `analysis/state/sample_manifest.json`.

By role, the sample has 57 shopper, 26 merchant and 17 support traces. By turn, it has 86 first turns, 10 second turns and 4 third turns. In open coding, 37 traces got at least one failure note and 63 were marked "no failure observed."

The 24 extra traces found in the Part D search are not part of these 100.

## How the modes appeared

batch1 produced four new modes: `unbacked_promise`, `wrong_resolution_route` (which merged my refund-route and missed-escalation notes), `irrelevant_or_contradictory_statement` and `missing_decision_info`. I kept the tone and length notes (0049, 0003 and 0082) as notes only.

batch2 added three more: `unflagged_missing_or_inconsistent_data`, `missing_policy_citation` and `unexplained_escalation`. I dropped `missing_decision_info` because it was a single note with no SPEC requirement behind it.

batch3 and batch4 added no new modes. In batch3, I added positives and close negatives to the existing modes. In batch4, the notes fitted existing modes, and I kept final-0142 ("Store 6") as a note only. So the final taxonomy has six modes, recorded in `analysis/state/patterns.json`.

## Taxonomy revision: `missing_policy_citation`

My first definition was: *"The reply makes a claim derived from a policy document (window, threshold, fee, cancellation rule) without citing that policy's identifier."*

The Part D search produced 18 suggestions. Accepting and rejecting them showed that the definition was too vague in three places:

1. **Numbers that aren't policy.** Four suggestions quoted a number with no citation. In final-0134, 0136 and 0137 the number was the user's own budget. In final-0184 it was a shipping time worked out from order dates. Neither is a policy claim, so I rejected them and kept them as close negatives.
2. **Citing in an earlier turn.** In final-0076, 0078, 0079 and 0080, turn 2 repeated a policy fact that turn 1 had cited with `cw-returns`. I decided the citation must appear in the same reply, because the user may only read the latest answer.
3. **Refund timing and auto-approval.** I decided these also count as policy facts.

The revised definition reads: *"The reply states a fact or rule that comes from a policy document (return or dispute window, refund threshold or auto-approval, refund timing, fee, cancellation rule) and does not name that policy's identifier in the same reply. A citation in an earlier turn does not count. Numbers that come from the user (such as a budget) or from order data (dates, totals, shipping time) are not policy claims."*

I reran the search with the revised definition. It found six more instances, and I accepted all of them. For those, I applied the rule that repeating a policy fact still needs the identifier, even when a tool result supplied it (for example, "5 to 10 business days" from `issue_refund`). Across both rounds there were 24 suggestions: I accepted 20 and rejected 4.

I made two other revisions. I renamed `irrelevant_or_contradictory_statement` to `statement_conflicts_with_order_status` after comparing my modes with the AgentDebug taxonomy. During labelling, I also moved final-0224 (`unbacked_promise`) and final-0225 (`wrong_resolution_route`) from close negatives to positives.

## Labels (Part E)

Every trace in the sample has a Pass or Fail for every mode, giving 600 labels. Each Fail has evidence. The labels are stored in `analysis/state/labels/` and as Langfuse scores (1 = fail), and the latest Langfuse score matches the local file for all 600.

| Mode | Fail | Pass | Fail rate in sample | SPEC |
| --- | --- | --- | --- | --- |
| missing_policy_citation | 22 | 78 | 22% | RESP-1 |
| unbacked_promise | 18 | 82 | 18% | RESP-3, RESP-1 |
| wrong_resolution_route | 17 | 83 | 17% | ESC-1 to ESC-4 |
| unflagged_missing_or_inconsistent_data | 13 | 87 | 13% | RESP-3 |
| statement_conflicts_with_order_status | 10 | 90 | 10% | RESP-3, RESP-5 |
| unexplained_escalation | 5 | 95 | 5% | RESP-4 |

59 of the 100 traces fail at least one mode. These rates describe this sample only. batch3 was targeted at finding failures, so they are not estimates for all traces.

Twenty-five traces I had first marked "no failure observed" received at least one Fail label, 10 of them for `missing_policy_citation`. Labelling each mode against a written definition caught problems that a single open read missed, especially after I tightened the citation rule.

## SPEC

I did not change `SPEC.md`. One possible gap is recorded in `patterns.json` under `spec_gaps`: in final-0139 T1, product price answers don't say whether delivery fees are extra. No requirement covers this.

