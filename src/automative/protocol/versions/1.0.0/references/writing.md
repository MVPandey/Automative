# How to write

Everything you write in a run gets read by a person later: commit messages, hypotheses, strategy
entries, notes, the end of run summary. Write it so that person understands it on the first read.

## Rules

1. Say what you did and why in plain words. "Replaced the list membership test with a set because
   the profile showed 60% of the time there." Not "Leveraged a set based approach to enhance lookup
   performance."
2. One idea per sentence. Keep most sentences under 20 words.
3. Use the same word for the same thing every time. Do not cycle synonyms.
4. Use "is", "has", "does". Do not write "serves as", "represents", "boasts", "features".
5. Active voice with a named actor. "The verifier timed out", not "A timeout was encountered".
6. Give the number. "Cut bench_ms from 9.1 to 5.0", not "significantly improved performance".
7. No em dashes. Use a comma, a period, or parentheses.
8. No emoji, no bold labels at the start of bullets, no headings in Title Case.
9. No filler openers or closers: "Let's dive in", "In conclusion", "I hope this helps", "Great
   question", "Here is a summary".
10. No hedge stacks. Pick one of "may", "might", "could", or say what you know.
11. Do not claim significance. A change is not "pivotal", "crucial", "robust", "seamless", or a
    "game changer". It moved a number or it did not.
12. Do not narrate the diff in commit messages ("This change adds..."). State the change:
    "Cache the parsed config across calls".

## Words to avoid

delve, leverage, utilize, robust, seamless, landscape, tapestry, testament, pivotal, crucial,
underscore, highlight (as a verb), foster, harness (as a verb), navigate (metaphor), elevate,
streamline, empower, journey, ecosystem (metaphor), synergy, holistic, cutting edge, game changer,
best practices, in order to, due to the fact that, it is important to note, at its core, the real
question is, not just X but Y.

When one of these is the exact technical term (a hash "landscape", a "robust" statistics estimator),
keep it. The rule is about padding, not vocabulary bans.

## Examples

Commit message, before: "Enhanced the deduplication logic by leveraging a set-based approach to
significantly improve lookup performance."
After: "Use a set for the dedupe membership test."

Hypothesis, before: "This should potentially help by possibly reducing overhead in the hot path."
After: "Membership tests are 60% of the profile; set lookup is O(1) instead of O(n)."

Strategy, before: "Consider exploring alternative data structures to unlock performance gains."
After: "When a hot loop does `x not in list`, switch the list to a set. Helped on i2 and i3."

Summary, before: "Overall this run was a success and showcases the power of iterative optimization."
After: "8 tries, 4 kept. bench_ms 9.137 to 0.034. The two big wins were sorted() and set(); the
last four tries were noise."
