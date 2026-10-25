# Interface comparison

The interface lives in `analysis/review_app/`. Before designing it, I reviewed 8 sessions (10 traces) in the standard Langfuse view: final-0039, 0063, 0050, 0212, 0155, 0085, 0151 and 0114.

## Kept from the reference interface: margin notes

The reference interface puts notes in a column next to the conversation, with each note attached to the passage it quotes. I kept this. In Langfuse, comments attach only to a whole trace. On final-0155 I wanted to comment on one sentence of the reply, but a trace-level comment didn't show which sentence I meant. In my interface, I select the text and the note is saved with that quote, which is highlighted in the reply.

## Changed after checking my traces: grouping turns by session

The reference interface shows one trace at a time. Cartwheel creates one trace per user turn, so a follow-up turn appears without the turns before it. On final-0039, turn 2 looked like a false claim that the refund was already done. It was actually correct, because the `issue_refund` call in turn 1 had returned `auto_approved`, and that result wasn't visible from turn 2. My interface groups traces by `cartwheel.session_id` and shows each conversation in order. Turns 2 and later also show an "Earlier in this session" strip with the earlier tool calls and their results.

## Remaining limitation: no interim model text

The traces don't record the model's text between tool calls, because the `openai.response` generation outputs are null in Langfuse. I can see each tool call, its result and the final reply for each turn, but not what the model said or reasoned before choosing a tool. When a wrong tool call happens, I can record that it happened but not why. Fixing this would mean changing the application's tracing, not the review interface.
    
