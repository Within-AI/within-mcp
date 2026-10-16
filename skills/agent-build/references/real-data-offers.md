# Real-data offers

Agent Build makes five offers whenever the access ledger shows real access to a system. Each offer names the
access state that makes it possible. The states come from the ledger: `extract` (a file exported from the system),
`read-only` (a connection that can read but not write) and `live` (the system the agent will run against).

## The five offers

1. **System extract and field profile.** Access state: `extract`. Profile the extract with
   `scripts/profile.py --run R --system S --file inputs/history/S.csv`. It writes `R/profiles/S.json` with per-field
   counts, null rates, top values and the date-parse share. A field with dates suggests pinning the clock in tests.
   A measure with nothing to count is recorded as needs more information.
2. **Replay of the rules that stop work against past cases.** Access state: `extract` (or `read-only`). Run
   `scripts/replay.py --run R --cases DIR --command CMD...` over past case files that carry the historical outcome of
   each rule. It writes `R/records/replay.json` with fire, no-fire, agree, disagree and needs-more-information counts
   per rule. There is no pass threshold: people read the counts. A missing historical outcome is never inferred.
3. **Real documents as test cases.** Access state: `extract`. Real documents (invoices, statements, forms) received
   from the customer become test cases in place of synthetic ones, with expected results agreed before the build.
4. **Real connectors, MCPs or APIs in the target.** Access state: `read-only` or `live`. The agreed target uses the
   real connector, MCP server or API instead of a stub, so the agent is tested against the system it will meet.
5. **Shadow mode before the agent closes anything.** Access state: `live`. The agent runs beside the people who do
   the work today and records what it would have done, without closing, posting or sending anything. Shadow mode
   runs on the customer's side, inside their systems.

## The no-access path

When the ledger shows no access, the build goes on without real data:

- Synthetic data is labelled as synthetic wherever it appears (case files, records, the hand-over).
- Every item that depends on the missing access is stamped needs more information.
- The check to run when access arrives is added to the hand-over list.
- No build stage refuses for missing access.
- An action that needs real data that is not available stops or escalates; it never runs on synthetic data.
