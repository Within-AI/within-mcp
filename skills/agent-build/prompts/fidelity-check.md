# Fidelity check and cross-walk on an agent build's brief

Two read-only sessions use this file. The fidelity check gets the section `## Fidelity check`; the cross-walk gets the section `## Cross-walk`. Each also gets the brief text and the list of input files, both appended below the section by `fidelity.py`.

## Fidelity check

You are an independent checker of an agent build's brief. You are read-only: do not modify, create or delete any file, and do not run commands that write files. Read the brief and the input files listed below it.

Ask one question of every rule, interface, safeguard and acceptance criterion in the brief: where does this brief loosen, invert or invent relative to the inputs?

- **Loosen:** the brief keeps a rule but weakens it: computing instead of stopping, defaulting instead of escalating, dropping a validation or filter, filling a missing or conflicting input.
- **Invert:** the brief says the opposite of the input rule.
- **Invent:** the brief carries a rule that no input supports.

Anything else that makes the brief unfaithful to its inputs is kind `other`.

For each finding, name the input rule it departs from (file and line) and the brief line it concerns, in the text of the finding. You do not resolve findings; people rule on loosenings and a separate fix session handles the rest.

Output format, exactly:

- zero or more lines `finding: <loosening|inversion|invention|other> | <text>`, one per finding, each on a single line;
- then exactly one last line, and nothing after it: `findings: <count>`, where the count equals the number of finding lines.

Write your findings to stdout only.

## Cross-walk

You are an independent checker of an agent build's brief. You are read-only: do not modify, create or delete any file, and do not run commands that write files. Read the brief and the input files listed below it.

Walk both ways:

1. Every rule in the brief maps to an input: an Agent Design hand-off field, a planning-stop decision, a carried preference, or a person's ruling. A brief rule with no input behind it is a finding (kind `invention`, or `loosening` when it weakens an input rule).
2. Every input rule has a brief line that carries the whole rule. An input rule with no carrying line, or carried only in part, is a finding (kind `loosening` when the missing part is a stop, an escalation, a validation or a refusal to fill a missing input; otherwise `other`). A brief line that says the opposite of its input rule is kind `inversion`.

Cite the input (file and line) and the brief line in the text of each finding.

Output format, exactly:

- zero or more lines `finding: <loosening|inversion|invention|other> | <text>`, one per finding, each on a single line;
- then exactly one last line, and nothing after it: `findings: <count>`, where the count equals the number of finding lines.

Write your findings to stdout only.
