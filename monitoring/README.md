# Homework 7 monitoring: unbacked_promise

Judge: unbacked_promise-v1 (frozen, gpt-4o-mini). Cartwheel model: claude-opus-4-6.
Periods: before = HW3 final run (2026-10-14), after = new run (2026-11-15). 50 scenarios each.
Random sample 20% (10 conversations); risk groups policy_lookup, write_action; threshold 0.10.

## 1. Did the corrected failure estimate move?
Probably, but the charts in Langfuse can’t show it. Both verdict series sit at zero until a single spike around 3 PM, when the run landed. The spike reads about 0.5 for unbacked_promise_verdict and about 0.75 for unbacked_promise_risk_verdict.
The flat zeros before the run probably mean no data rather than zero failures. The flagged chart counts flagged scores across both periods:40 = 21 + 19 flagged risk verdicts, 10 = 5 + 5 flagged random verdicts. Each trace gets at most one score
of each kind; there are 27 risk traces and 10 random traces per period.

## 2. Do the intervals support a conclusion?
Only a weak one. If the verdict is a 0/1 score, an average of about 0.5 on 10 conversations gives a 95% interval of roughly 0.24 to 0.76. That’s far above the 0.10 threshold that I set, so the sample points to a failure rate well above it. I
learned that this is an uncorrected interval on the raw rate. The monitor's corrected 95% intervals are 0.00–0.52 (before) and 0.00–0.50 (after), and both include 0.10. I still think that the interval is too wide to pin down the rate, and
without the HW3 baseline there’s nothing to compare it against. 

## 3. What did the risk groups reveal?
Nothing yet unfortunately. These charts combine all risk groups and don’t split out policy_lookup or write_action. I'd probably need to filter by group to answer this. The per-group counts are in
monitoring/output/{before,after}/verdicts_split.json

## 4. What happens if the estimate crosses the threshold?
The dashboard doesn't say what action the threshold triggers, so I'd probably need to do error analysis on the flagged traces, then turn confirmed failures into new evaluation cases in the HW6
test suite. And the corrected estimate (0.00) is below 0.10 in both periods. I'd treat the crossing as a result to review, not a settled failure, until the baseline comparison and the per-group breakdown are in. 

