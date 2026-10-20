# Concepts for optimising concurrent coding-agent sessions on one repository

Written 2026-10-19 for the colony: one Master session and many worker sessions
on a shared repository.

## How to read this

Every entry carries a **foundation**. Where an author and year appear, the
attribution is one I am confident of. Where only a field or an algorithm name
appears, I did not verify a specific paper, and the entry says so. Nothing
here cites a paper I could not name without guessing. Agent-specific
applications are labelled **proposed adaptation** unless a source is given.

Four objectives are kept apart, because almost nothing improves all four:

| Tag | Objective |
|---|---|
| TOK | fewer billed tokens |
| LAT | lower wall-clock time |
| COR | correctness, less rework |
| THR | throughput, more verified changes an hour |

**One warning that governs the whole list.** In this colony, cache reads are
98.3% of 9.95B raw tokens, and our own fits put their plan-limit weight at 0 to
0.05 (`reviews/weekly-limit-weights-2026-10-06/report.md`). So a TOK claim that
rests on cache reads is worth between almost nothing and a great deal, and we
cannot yet say which. Each TOK entry says which token kind it moves:
**cache reads**, **cache writes** (material entering a context), or **output**.

## Search taxonomy

The space was walked along 18 axes. Each axis asks a different question about
the same system.

| # | Category | The question it asks |
|---|---|---|
| 1 | Context economics and rate-distortion | What is the least context that still does the job? |
| 2 | Memoisation, content addressing, incremental computation | What has already been computed? |
| 3 | Compiler and program-analysis methods | How should the work be cut into tasks? |
| 4 | Scheduling theory and queueing | In what order, on how many workers? |
| 5 | Concurrency control and conflict resolution | Who may write what, and when? |
| 6 | Decision theory and value of information | Is the next call worth its cost? |
| 7 | Bandits, reinforcement learning, meta-learning | How does the policy improve itself? |
| 8 | Information-theoretic retrieval | Which read tells us the most for each token? |
| 9 | Model routing and inference budget | Which model, at what depth? |
| 10 | Verification and proof-carrying artifacts | How is an output trusted without re-reading it? |
| 11 | Provenance and epistemic state | What is known, assumed, or unresolved? |
| 12 | Fault tolerance and recovery | What happens when a worker fails midway? |
| 13 | Query-optimisation analogues | How would a database planner run this? |
| 14 | Distributed-systems protocols | How do the agents agree? |
| 15 | Synthesis, partial evaluation, workflow extraction | What should stop being a model call? |
| 16 | Inference and serving layer | What is available to a hosted client, and what is not? |
| 17 | Control theory and adaptive allocation | How does the system hold itself at a set point? |
| 18 | Measurement and causal inference | How is any of this proved? |

## 1. Context economics and rate-distortion

**1.1 Rate-distortion bound on a task's context**
- *Mechanism:* treat the brief as a lossy code for the repository state a worker needs. Seek the smallest code whose distortion still passes the acceptance criteria.
- *Foundation:* Shannon rate-distortion theory; the Blahut-Arimoto algorithm computes the curve.
- *Application:* measure, for a sample of past tasks, the smallest brief that still produced a PASS, and fit the curve for this repository.
- *Benefit:* TOK, cache writes and cache reads.
- *Difficulty:* high.
- *Maturity:* established theory, proposed adaptation to briefs.
- *Novelty:* makes "write a short brief" an optimisation with a measurable floor instead of a style preference.
- *Limits:* distortion needs a scalar proxy for "passes the criteria", which is itself a judgement.

**1.2 Information bottleneck for handoff artifacts**
- *Mechanism:* compress a predecessor's findings while keeping the part predictive of the successor's decisions.
- *Foundation:* the information bottleneck, Tishby, Pereira and Bialek.
- *Application:* learn which handoff fields actually changed what the next worker did, and drop the rest.
- *Benefit:* TOK, cache writes.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* gives a criterion for what belongs in a handoff, rather than a template.
- *Limits:* needs many handoff-and-outcome pairs. Colony has almost none.

**1.3 Minimum description length for repository summaries**
- *Mechanism:* choose the summary that minimises its own length plus the length of the corrections it later forces.
- *Foundation:* minimum description length, Rissanen.
- *Application:* score candidate repository maps, such as graft's output, by the follow-up reads they fail to prevent.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established theory, proposed adaptation.
- *Novelty:* penalises a summary for the reads it does not prevent, which is the cost a token count misses.
- *Limits:* needs per-task logging of reads that followed a summary.

**1.4 Progressive disclosure with a deferred schema**
- *Mechanism:* publish tool and instruction names first and load full schemas only on demand.
- *Foundation:* lazy loading; the same idea as demand paging.
- *Application:* already live in this host, which withholds deferred tool schemas until a lookup. Extend it to skills and rules.
- *Benefit:* TOK, cache reads, because the startup context rides every request.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little, but the measured size is large: colony workers start at a median 44,592 tokens.
- *Limits:* a lookup costs a request, so it loses when the deferred item is almost always needed.

**1.5 Context compilation to a typed slice**
- *Mechanism:* build each task's context from a declared input list resolved against the code graph, instead of passing a transcript.
- *Foundation:* program slicing, Weiser 1981.
- *Application:* the brief's Inputs section becomes a machine-resolved slice, with graft supplying the symbols and their spans.
- *Benefit:* TOK, cache writes; COR, because a worker reads the right lines.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* the slice is computed from the dependency graph, not chosen by a person.
- *Limits:* slicing is only as good as the index; a stale index misleads.

**1.6 Attention-order placement for cache friendliness**
- *Mechanism:* put the stable prefix first and the volatile part last, so a change invalidates as little of the cache as possible.
- *Foundation:* prefix caching in serving systems; the same principle as a shared immutable prefix in a radix tree.
- *Application:* order a worker's context as system, then project rules, then brief, then volatile state.
- *Benefit:* TOK, cache writes, and LAT.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* standard in serving work, rarely stated for agent prompt assembly.
- *Limits:* measured here as already solved. Fresh input is 0.0010% of colony worker input, so the prefix cache already hits on nearly everything.

**1.7 Semantic deduplication across concurrent contexts**
- *Mechanism:* detect that two live workers hold the same material and keep it in one of them.
- *Foundation:* content-addressable storage and deduplicating backup systems.
- *Application:* a shared read service returns a digest and a pointer when a sibling already holds the content.
- *Benefit:* TOK in aggregate, cache writes.
- *Difficulty:* high.
- *Maturity:* speculative adaptation.
- *Novelty:* treats the colony's contexts as one storage system with a dedup layer.
- *Limits:* a hosted agent cannot read another session's context, so this needs a proxy that rewrites tool results.

**1.8 Lossy observation ageing**
- *Mechanism:* replace old tool results with short placeholders while keeping the actions and the reasoning.
- *Foundation:* reported in agent-harness work as observation truncation; mechanically the same as a ring buffer with summaries.
- *Application:* a proxy rewrites a worker's older tool results after N steps.
- *Benefit:* TOK, cache reads.
- *Difficulty:* medium.
- *Maturity:* emerging research.
- *Novelty:* ages by position rather than by size, which matches how cost accrues.
- *Limits:* cuts only cache reads, so its value depends on the unmeasured weight. A worker that needs an old result must fetch it again.

## 2. Memoisation, content addressing and incremental computation

**2.1 Action-cache keyed on a hermetic fingerprint**
- *Mechanism:* key a completed unit of work on the hash of its inputs, instructions, model id and tool versions. Reuse the result on a hit.
- *Foundation:* Bazel's action cache and Nix's derivations.
- *Application:* a task whose brief, revision and model all match an earlier one returns the stored result.
- *Benefit:* TOK, all kinds, and LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready in build systems, proposed adaptation here.
- *Novelty:* makes reuse a function of a fingerprint, not of a model's judgement that two tasks look alike.
- *Limits:* a model is not deterministic, so the hit returns a *previous good* answer, not *the* answer. Measured in this repository: cross-task refetch is 0.16% of material, so the hit rate would be low.

**2.2 Self-adjusting computation over the repository graph**
- *Mechanism:* record the dependency trace of a derived fact, then recompute only the parts whose inputs changed.
- *Foundation:* self-adjusting computation, Acar; differential dataflow.
- *Application:* a stored analysis names the files and symbols it read; a commit touching none of them leaves it valid.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* gives invalidation a proof rather than a time-to-live.
- *Limits:* needs every derived fact to carry its read set, which means tool results must be captured, not summarised.

**2.3 Merkle-keyed artifact store with explicit invalidation**
- *Mechanism:* store findings under the Merkle root of the subtree they describe.
- *Foundation:* Merkle trees; Git's own object model.
- *Application:* a worker asks "is there a finding for `src/auth` at this tree hash?" before reading.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* production-ready primitives, proposed adaptation.
- *Novelty:* invalidation is exact at subtree granularity.
- *Limits:* the 2026-10-11 analysis costed a findings file at 2.1x to 8.3x the rediscovery it would save. So the store must hold conclusions, not file contents.

**2.4 Function-level memoisation of tool calls**
- *Mechanism:* cache a tool result on the pair of command and repository state.
- *Foundation:* classical memoisation; ccache and sccache do this for compilers.
- *Application:* a proxy in front of Bash returns the stored stdout when the command and tree hash match.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little.
- *Limits:* measured out here. Identical call with an identical result inside one worker is 414 of 28,692 calls, 1.44%.

**2.5 Semantic caching with an applicability check**
- *Mechanism:* reuse a result for an equivalent, not identical, task, then validate it against the current state before trusting it.
- *Foundation:* semantic caching in database query processing.
- *Application:* "find the callers of X" reuses an earlier answer if the call graph around X is unchanged.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established in databases, proposed adaptation.
- *Novelty:* the validation step is what separates it from a plain cache.
- *Limits:* equivalence between two natural-language tasks has no sound test. A wrong hit is worse than a miss.

**2.6 Common-subexpression elimination across sibling workers**
- *Mechanism:* detect that two tasks will both derive the same fact and compute it once.
- *Foundation:* common-subexpression elimination in compilers.
- *Application:* the Master sees two briefs naming the same input and hoists the read into one shared pre-task.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* hoisting happens at planning time, before any token is spent.
- *Limits:* measured small here: cross-task file refetch is 0.16% of material. It may be larger for reasoning, which leaves no tool call to count.

**2.7 Materialised views over the code graph**
- *Mechanism:* keep derived summaries that answer common questions, and refresh them on change.
- *Foundation:* materialised views and view maintenance in databases.
- *Application:* graft's index is exactly this. Extend it with per-module contracts and test maps.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* little, but the measured gap is stark. Workers made zero graft, Grep or Glob calls against 24,187 Bash calls.
- *Limits:* a stale view is worse than none, which is why `colony start` now refuses a stale index.

**2.8 Trace-based specialisation of repeated agent paths**
- *Mechanism:* record hot sequences of tool calls and replace them with one compiled step.
- *Foundation:* trace-based just-in-time compilation; inline caching.
- *Application:* a repeated five-command shell probe becomes one script.
- *Benefit:* TOK, output and cache writes; LAT, by removing whole requests.
- *Difficulty:* medium.
- *Maturity:* established in runtimes, proposed adaptation.
- *Novelty:* the unit of optimisation is a sequence, not a prompt.
- *Limits:* needs a trace log and a guard that detects when the trace no longer applies.

## 3. Compiler and program-analysis methods for decomposition

**3.1 Typed task DAG with a deterministic runtime**
- *Mechanism:* compile a request into a graph of typed tasks, then let a non-model runtime schedule, cache, retry and validate.
- *Foundation:* dataflow graphs; build systems such as Bazel; Kahn process networks for the streaming case.
- *Application:* colony already holds briefs, task state, an event log, verdicts and `retry_of`. The missing piece is the runtime.
- *Benefit:* TOK, because the Master's own reasoning stops being the scheduler; THR.
- *Difficulty:* high.
- *Maturity:* production-ready in build systems, proposed adaptation.
- *Novelty:* moves coordination out of the dearest context in the system. Measured: the Master spends a median 21 responses and 6.6M tokens between consecutive task starts.
- *Limits:* a DAG fixed in advance cannot absorb a discovery that changes the plan, so it needs an escape to the Master.

**3.2 Program-dependence partitioning of work**
- *Mechanism:* cut tasks along edges of the program dependence graph, so two tasks share no data or control dependency.
- *Foundation:* the program dependence graph, Ferrante, Ottenstein and Warren 1987.
- *Application:* the Master partitions a change by module and interface boundary rather than by file count.
- *Benefit:* COR, less merge rework; THR.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* the partition is derived from the code, not from a model's guess at independence.
- *Limits:* needs a call graph across languages. graft provides one for the indexed set.

**3.3 Blast-radius bounding before assignment**
- *Mechanism:* compute what depends on the lines a task will touch, and refuse an assignment whose radius overlaps another live task.
- *Foundation:* reverse reachability on the call graph; `graft blast` implements it.
- *Application:* `colony start` rejects a brief whose blast radius intersects an open task's.
- *Benefit:* COR; THR.
- *Difficulty:* low, since the tool exists.
- *Maturity:* production-ready.
- *Novelty:* conflict prevention at admission, not at merge.
- *Limits:* radius is static; a dynamic dispatch or a config string escapes it.

**3.4 Abstract interpretation for a cheap pre-check**
- *Mechanism:* run a sound over-approximate analysis before spending a model call, to rule out whole classes of answer.
- *Foundation:* abstract interpretation, Cousot and Cousot.
- *Application:* a type check or a lint pass answers "can this symbol be null here" without a model.
- *Benefit:* TOK, output; LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready tooling, proposed adaptation as a gate.
- *Novelty:* uses a sound analysis to shrink the question the model is asked.
- *Limits:* over-approximation yields "maybe", which still needs the model.

**3.5 Counterexample-guided refinement of a brief**
- *Mechanism:* start with a coarse specification, let a cheap check find a counterexample, and refine only where the check failed.
- *Foundation:* CEGAR, Clarke, Grumberg, Jha, Lu and Veith 2000.
- *Application:* a brief states criteria; a failing test is the counterexample that refines the next brief, not a fresh full specification.
- *Benefit:* TOK, cache writes and output; COR.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* refinement is driven by an actual failure, so no token is spent on detail nothing contradicted.
- *Limits:* needs a checker fast and sound enough to drive the loop.

**3.6 Partial-order reduction over independent task orders**
- *Mechanism:* explore one representative order when several orders of independent actions give the same outcome.
- *Foundation:* partial-order reduction in model checking, Godefroid.
- *Application:* the Master stops considering orderings of tasks that touch disjoint modules.
- *Benefit:* TOK, output; LAT.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* names a specific waste in planning.
- *Limits:* no evidence colony does this at all, so there is nothing to reduce today.

**3.7 Interface-first decomposition with contracts**
- *Mechanism:* fix the contract between two tasks before either runs, so neither needs the other's code.
- *Foundation:* design by contract, Meyer; interface-based design.
- *Application:* the brief for each side carries the agreed signature and invariants.
- *Benefit:* COR; TOK, cache writes, because neither side reads the other.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little in software terms, under-used in agent briefs.
- *Limits:* a wrong contract costs two reworks instead of one.

**3.8 Call-graph-aware test selection**
- *Mechanism:* run only the tests reachable from the changed lines.
- *Foundation:* regression test selection, Rothermel and Harrold.
- *Application:* the worker's "run the tests that apply" becomes a computed set rather than a judgement.
- *Benefit:* LAT; TOK, cache writes, because less test output enters a context.
- *Difficulty:* medium.
- *Maturity:* established research with production implementations.
- *Novelty:* replaces a judgement call in the prompt with a derivation.
- *Limits:* unsound when a test depends on state outside the graph, so a full run is still needed before a merge.

## 4. Scheduling theory and queueing

**4.1 Critical-path scheduling of the task DAG**
- *Mechanism:* schedule tasks on the longest dependency chain first, so the chain sets the finish time.
- *Foundation:* the critical path method; list scheduling with Graham's bound.
- *Application:* the Master starts the critical chain before any leaf task.
- *Benefit:* LAT; THR.
- *Difficulty:* low once a DAG exists.
- *Maturity:* production-ready.
- *Novelty:* little in operations research, absent from colony, which starts tasks in the order a person thought of them.
- *Limits:* task durations are unknown in advance. Measured span is a median 30 minutes with a mean of 64, so the estimate is noisy.

**4.2 Heterogeneous earliest-finish-time assignment**
- *Mechanism:* assign each task to the processor that finishes it soonest, given differing speeds.
- *Foundation:* HEFT, list scheduling for heterogeneous processors.
- *Application:* a worker's model is its processor. Route a task to the model whose expected finish is earliest, not the cheapest.
- *Benefit:* LAT; THR.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* treats model choice as a scheduling decision, not a cost decision.
- *Limits:* pulls against TOK. A faster model is usually dearer.

**4.3 Work stealing among idle workers**
- *Mechanism:* an idle worker takes work from a busy one's queue.
- *Foundation:* work stealing, Blumofe and Leiserson, as in Cilk.
- *Application:* a worker that finishes early claims the next ready task instead of closing.
- *Benefit:* THR; LAT.
- *Difficulty:* high for sessions, because a session's context is specialised to its task.
- *Maturity:* production-ready in runtimes, speculative for agents.
- *Novelty:* inverts colony's one task for each worker rule.
- *Limits:* the rule exists for a measured reason, and stealing would carry the first task's context into the second.

**4.4 Little's law as a concurrency set point**
- *Mechanism:* hold work in progress at throughput multiplied by latency, so the queue neither starves nor swells.
- *Foundation:* Little's law.
- *Application:* a worker span of a median 30 minutes, against a target of one verified change an hour, gives the worker count directly.
- *Benefit:* THR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* replaces "run a few workers" with an arithmetic set point.
- *Limits:* assumes a stable arrival rate, which a human-driven colony does not have.

**4.5 Admission control under a shared quota**
- *Mechanism:* refuse to start work that the remaining budget cannot finish.
- *Foundation:* admission control and load shedding in queueing systems.
- *Application:* `colony start` refuses a new worker when the five-hour window is nearly spent, since a worker stopped mid-task wastes everything it spent.
- *Benefit:* TOK, all kinds, by avoiding total loss; COR.
- *Difficulty:* medium.
- *Maturity:* production-ready pattern, proposed adaptation.
- *Novelty:* treats the usage window as a resource with a reservation, which practitioners report doing by hand.
- *Limits:* needs a reliable read of the remaining window. The weight of each token kind in that window is unpublished.

**4.6 Backpressure from the integration stage**
- *Mechanism:* let the slowest downstream stage throttle the upstream one.
- *Foundation:* backpressure routing, Tassiulas and Ephremides 1992; flow control in streaming systems.
- *Application:* review and merge is the bottleneck, so the number of open tasks is capped by the review queue, not by available panes.
- *Benefit:* THR; COR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* names review as the constrained resource, which the session-management literature also reports.
- *Limits:* needs the review stage to be measured. Colony measures the worker side only.

**4.7 Gang scheduling of tasks that must agree**
- *Mechanism:* start a set of tasks together when none is useful alone.
- *Foundation:* gang scheduling in parallel operating systems.
- *Application:* a change to a contract and both sides of it start as one gang, or not at all.
- *Benefit:* COR; LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready concept, proposed adaptation.
- *Novelty:* avoids a half-landed interface change, which an independence-only scheduler permits.
- *Limits:* holds capacity idle until the whole gang can run.

**4.8 Tail-latency hedging with cancellation**
- *Mechanism:* start a second attempt once the first exceeds a high percentile, and cancel the loser.
- *Foundation:* "The Tail at Scale", Dean and Barroso 2013.
- *Application:* a worker past the 95th percentile of span gets a parallel retry.
- *Benefit:* LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready in serving, proposed adaptation.
- *Novelty:* treats a stuck agent as a tail-latency problem with a known remedy.
- *Limits:* strictly negative for TOK under a shared quota. Measured: worker span mean is 64 minutes against a median of 30, so the tail is real. Cancellation must be safe, and a worker that already wrote files cannot simply be dropped.

## 5. Concurrency control and conflict resolution

**5.1 Ownership partitioning with affinity routing**
- *Mechanism:* give each file or module exactly one writer for the life of a task.
- *Foundation:* ownership in concurrent systems; sticky routing with consistent hashing.
- *Application:* the brief declares the paths the worker owns, and `colony start` rejects an overlap.
- *Benefit:* COR; THR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little, and it is colony's clearest gap. Workers share one tree with no ownership today.
- *Limits:* a change that needs two owners must be serialised or gang scheduled.

**5.2 Optimistic concurrency with a validation phase**
- *Mechanism:* let workers proceed in isolation and validate at commit, aborting on a conflict.
- *Foundation:* optimistic concurrency control, Kung and Robinson; snapshot isolation.
- *Application:* each worker in a worktree; a validator rejects a merge whose read set changed underneath it.
- *Benefit:* THR; COR.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* makes the abort explicit instead of discovering it in a merge conflict.
- *Limits:* an abort wastes the whole task's tokens, which is dear when a task costs millions.

**5.3 Single committer with a fencing token**
- *Mechanism:* one process performs every commit, holding a lease with a monotonic token so a stale holder is rejected.
- *Foundation:* leases with fencing tokens, as described in distributed-systems practice; `index.lock` is Git's own crude version.
- *Application:* workers write files and never call git; colony commits.
- *Benefit:* COR.
- *Difficulty:* medium.
- *Maturity:* production-ready, and the pattern a session-management tool reports using.
- *Novelty:* removes a class of fault rather than reducing its chance.
- *Limits:* colony workers call git today, so this changes the worker contract.

**5.4 Git worktrees as isolation units**
- *Mechanism:* each worker gets its own checkout on its own branch.
- *Foundation:* Git worktrees.
- *Application:* `herdr worktree create --branch --base --path`, or Claude Code's own `-w/--worktree`, both verified present.
- *Benefit:* COR; THR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none; it is standard practice that colony does not use.
- *Limits:* two worktrees cannot hold the same branch. Dependencies and `.env` files do not carry over. An index per worktree must be built, or graft must point at the main tree.

**5.5 Conflict-free replicated types for shared state**
- *Mechanism:* use data types whose concurrent updates merge without coordination.
- *Foundation:* CRDTs, Shapiro, Preguica, Baquero and Zawirski 2011.
- *Application:* the ledger, the event log and the findings store become grow-only sets, so concurrent workers never conflict on them.
- *Benefit:* COR; THR.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* applies to colony's own state, not to source code.
- *Limits:* source code is not a CRDT. Colony already appends to its event log under a lock, which is the cheap version.

**5.6 Sagas for multi-task changes**
- *Mechanism:* model a multi-task change as a sequence with a compensating action for each step, instead of one transaction.
- *Foundation:* sagas, Garcia-Molina and Salem 1987.
- *Application:* a four-task feature records how to undo each landed piece, so a failure at the third does not leave the tree half-changed.
- *Benefit:* COR.
- *Difficulty:* high.
- *Maturity:* production-ready in services, proposed adaptation.
- *Novelty:* gives partial failure a defined outcome.
- *Limits:* writing a compensation for a code change is itself work, and often a revert.

**5.7 Semantic merge over syntactic merge**
- *Mechanism:* merge by abstract syntax tree and declaration identity rather than by line.
- *Foundation:* semantic and structured merge research; three-way merge for the baseline.
- *Application:* two workers adding different functions to one file merge cleanly.
- *Benefit:* COR, less rework; THR.
- *Difficulty:* high.
- *Maturity:* emerging research with some tooling.
- *Novelty:* removes the main reason to forbid two writers in one file.
- *Limits:* language-specific, and a clean merge is not a correct one.

**5.8 Read-set recording for stale-decision detection**
- *Mechanism:* record what a worker read, and flag a result whose read set has since changed.
- *Foundation:* validation in optimistic concurrency; the same mechanism as 2.2.
- *Application:* colony already records `revision` and `dirty`. A read set would tell the Master whether a PASS still holds.
- *Benefit:* COR.
- *Difficulty:* medium.
- *Maturity:* established, proposed adaptation.
- *Novelty:* answers a limit already recorded in `colony.md`: the dirty flag does not say how the tree differed.
- *Limits:* the worker must capture reads, which a hosted agent does only in its transcript.

## 6. Decision theory and value of information

**6.1 Expected value of information before a call**
- *Mechanism:* estimate how much a prospective read or model call would change the decision. Skip it when the change is worth less than its cost.
- *Foundation:* value of information, Howard 1966.
- *Application:* a worker asks whether reading one more file could change which branch of the brief it takes.
- *Benefit:* TOK, output and cache writes.
- *Difficulty:* high.
- *Maturity:* established theory, proposed adaptation.
- *Novelty:* gives "stop exploring" a criterion instead of a style note.
- *Limits:* needs a prior over outcomes. A model's own estimate of its information gain is unvalidated.

**6.2 Optimal stopping for exploration depth**
- *Mechanism:* stop searching when the expected gain from one more look falls below its cost.
- *Foundation:* optimal stopping; the secretary problem for the pure-ranking case.
- *Application:* the explore budget shipped on 2026-10-19 is a fixed-horizon crude form: 20 own calls, then delegate.
- *Benefit:* TOK, output and cache writes.
- *Difficulty:* medium for a fixed rule, high for an adaptive one.
- *Maturity:* established theory; the fixed form is production-ready.
- *Novelty:* the adaptive version reacts to what the search has found so far.
- *Limits:* 49% of worker responses only read, so the rule has a large surface. A bad rule therefore does a lot of damage.

**6.3 Gittins index for allocating effort among open tasks**
- *Mechanism:* compute an index for each independent task and always advance the highest.
- *Foundation:* the Gittins index for multi-armed bandits, Gittins 1979.
- *Application:* a Master with several partly done tasks advances the one whose index is highest, rather than the one it looked at last.
- *Benefit:* THR; TOK indirectly, by abandoning poor tasks sooner.
- *Difficulty:* research-level.
- *Maturity:* established theory, speculative adaptation.
- *Novelty:* optimal for the independent case, which colony's independent tasks approximate.
- *Limits:* needs a reward model per task. Colony has verdicts only from 2026-10-11, with 160 PASS against 51 FAIL.

**6.4 Metareasoning over the next action**
- *Mechanism:* spend a small amount of computation deciding what computation to do next.
- *Foundation:* metalevel rationality, Russell and Wefald 1991.
- *Application:* a cheap model decides whether the dear model should think, read or write next.
- *Benefit:* TOK, output.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* separates the decision about work from the work.
- *Limits:* the metareasoner is itself a model call, so it must be much cheaper than what it governs. Measured: a sub-agent request costs 87,713 tokens against a worker's 208,114, so the ratio is only about two to one.

**6.5 Bayesian experimental design for the next read**
- *Mechanism:* choose the read that maximises expected information gain about the open question.
- *Foundation:* Bayesian experimental design; expected information gain.
- *Application:* among candidate files, read the one whose content most changes the posterior over where the fault lies.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* high.
- *Maturity:* established theory, proposed adaptation.
- *Novelty:* turns debugging into a designed experiment.
- *Limits:* needs a model over fault locations. A code graph gives a usable prior.

**6.6 Knowledge-gradient allocation across workers**
- *Mechanism:* allocate the next unit of budget where it most improves the final choice.
- *Foundation:* the knowledge-gradient policy, Frazier and Powell.
- *Application:* when two workers explore rival designs, give the next budget to the one whose result would most change the decision.
- *Benefit:* TOK; COR.
- *Difficulty:* research-level.
- *Maturity:* established research, speculative adaptation.
- *Novelty:* an explicit rule for a judgement a Master makes by feel.
- *Limits:* only applies when rival approaches are running, which colony rarely does.

**6.7 Anytime and contract algorithms for bounded work**
- *Mechanism:* design a task to produce a usable answer at any interruption, improving with time.
- *Foundation:* anytime and contract algorithms, Zilberstein.
- *Application:* a brief asks for a usable partial result at every checkpoint, so a budget cut never yields nothing.
- *Benefit:* COR under a budget; TOK, by making early stopping safe.
- *Difficulty:* medium.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* this is the property the reverted request budget lacked. A worker cut at 40 calls had no usable partial result, which is why 94% handing over was dangerous.
- *Limits:* some tasks have no meaningful partial answer, such as a migration that must be whole.

**6.8 Deferral and abstention as a first-class outcome**
- *Mechanism:* let a worker return "I cannot settle this for less than X" instead of guessing.
- *Foundation:* learning to defer; the reject option in classification.
- *Application:* colony already provides BLOCKED and QUESTION. Measured: 32 questions over 22 of 88 tasks, answered in a median 24 seconds.
- *Benefit:* COR; TOK, by avoiding a confident wrong path.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* little, but the measurement supports it: a worker that asks is cheaper than one that guesses.
- *Limits:* a worker that defers too readily pushes cost into the Master, whose request costs 348,563 tokens.

## 7. Bandits, reinforcement learning and meta-learning

**7.1 Contextual bandits over orchestration strategies**
- *Mechanism:* treat each strategy choice as an arm, with task features as context, and favour arms that verify well per token.
- *Foundation:* LinUCB, Li, Chu, Langford and Schapire 2010; Thompson sampling, Thompson 1933.
- *Application:* choose model, effort, explore budget and worker count per task from past verified outcomes.
- *Benefit:* TOK; COR.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* replaces a fixed policy file with a learned one.
- *Limits:* premature here. The labelled set is 160 PASS against 51 FAIL, and attribution is still in raw tokens, so it would learn the wrong objective.

**7.2 Successive halving over rival approaches**
- *Mechanism:* start several cheap attempts, keep the best half at each round, and concentrate budget.
- *Foundation:* successive halving and Hyperband, Li, Jamieson, DeSalvo, Rostamizadeh and Talwalkar 2017.
- *Application:* three cheap workers sketch rival designs; one continues.
- *Benefit:* COR; LAT.
- *Difficulty:* medium.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* buys a better choice with a bounded multiple of the budget.
- *Limits:* costs more tokens than a single attempt. It pays only when rework is dearer than exploration.

**7.3 Off-policy evaluation of a new policy from old logs**
- *Mechanism:* estimate how a proposed policy would have performed, using logged decisions and importance weighting.
- *Foundation:* inverse propensity scoring; doubly robust estimation, Dudik, Langford and Li.
- *Application:* evaluate a new model-routing rule against the 243 archived sessions without running anything.
- *Benefit:* TOK, because the evaluation costs no model calls.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* this is the cheap answer to the measured problem that a controlled benchmark here costs about 49 paired runs and near a billion tokens.
- *Limits:* needs the logged policy's randomness. Colony's past choices were deterministic judgements, so propensities are unknown and the estimate is biased.

**7.4 Reward shaping on verified change, not on PASS**
- *Mechanism:* define the learning signal as an integrated, accepted change, not a worker's self-report.
- *Foundation:* reward design in reinforcement learning; the general hazard of a proxy reward.
- *Application:* colony already separates a worker's PASS from the Master's verdict, recorded since 2026-10-11.
- *Benefit:* COR.
- *Difficulty:* low to record, high to learn from.
- *Maturity:* production-ready for the record.
- *Novelty:* the separation already exists and nothing reads it yet, which `colony.md` lists as an open point.
- *Limits:* a verdict is a human judgement, so the signal is sparse and slow.

**7.5 Meta-learning the decomposition policy**
- *Mechanism:* learn from past task graphs how finely to cut a new request.
- *Foundation:* meta-learning; learning to search.
- *Application:* the Master predicts the piece count from features of the request.
- *Benefit:* TOK; THR.
- *Difficulty:* research-level.
- *Maturity:* speculative adaptation.
- *Novelty:* attacks the measured failure of the 2026-10-11 split, where pieces came out as long as whole tasks.
- *Limits:* needs many labelled decompositions. Colony has about 232 tasks in total.

**7.6 Best-arm identification for model selection on a task class**
- *Mechanism:* spend a fixed budget deciding which model is best for a recurring task class, then exploit it.
- *Foundation:* best-arm identification in bandits.
- *Application:* decide once whether Sonnet suffices for documentation tasks.
- *Benefit:* TOK, output.
- *Difficulty:* medium.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* bounds the exploration cost of a routing decision.
- *Limits:* measured warning: the seven Sonnet workers in the archive are not a fair comparison, since nothing says they drew tasks of equal size.

**7.7 Bandit-driven retry policy**
- *Mechanism:* learn whether a failed task is better retried as-is, re-briefed, or escalated to a dearer model.
- *Foundation:* bandits with structured arms.
- *Application:* `retry_of` already records lineage.
- *Benefit:* TOK; COR.
- *Difficulty:* medium.
- *Maturity:* proposed adaptation.
- *Novelty:* measured basis: ten of fourteen FAILs in the 2026-10-11 archive had no successor at all, so retry policy is currently ad hoc.
- *Limits:* a FAIL has many causes, and the arm set must separate them.

**7.8 Curriculum ordering of a backlog**
- *Mechanism:* order tasks so earlier ones produce artifacts that make later ones cheaper.
- *Foundation:* curriculum learning, Bengio and colleagues; precedence constrained scheduling for the hard version.
- *Application:* run the task that builds the shared contract before the two that consume it.
- *Benefit:* TOK, cache writes; THR.
- *Difficulty:* medium.
- *Maturity:* established idea, proposed adaptation.
- *Novelty:* orders a backlog by the context it creates, not by priority alone.
- *Limits:* overlaps 3.1 and 4.1. The distinction is that this orders *independent* tasks by their side benefit, while critical-path scheduling orders *dependent* ones.

## 8. Information-theoretic retrieval

**8.1 Maximal marginal relevance for read selection**
- *Mechanism:* pick the next read that is relevant and least redundant with what is already held.
- *Foundation:* maximal marginal relevance, Carbonell and Goldstein 1998.
- *Application:* a retrieval tool ranks candidate files against the worker's current context, not against the query alone.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* redundancy is scored against the live context, which ordinary search ignores.
- *Limits:* needs an embedding of the held context. `docs-search` has local embeddings and could supply the machinery.

**8.2 Submodular coverage of the question**
- *Mechanism:* choose a read set that maximises coverage of the question under a token budget, exploiting diminishing returns.
- *Foundation:* submodular maximisation with a cardinality constraint; the greedy algorithm attains 1 - 1/e, Nemhauser, Wolsey and Fisher 1978.
- *Application:* select the k file spans that together cover the brief's inputs best.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established algorithm, proposed adaptation.
- *Novelty:* gives a provable guarantee on a step agents do by feel.
- *Limits:* coverage must be defined over something measurable, such as symbols named in the brief.

**8.3 Signature-level retrieval before body retrieval**
- *Mechanism:* read signatures first and bodies only where the signature is insufficient.
- *Foundation:* interface-first reading; `graft skeleton` and `graft_file_api` implement it.
- *Application:* already available and unused. Measured: Read results average 4,902 tokens a call across all sessions, against 105 for a graft CLI call.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none, which makes the measured gap the story: zero graft MCP calls in 227 colony workers.
- *Limits:* a signature hides behaviour, so some tasks must read the body.

**8.4 Range reads with an explicit limit**
- *Mechanism:* read a span, not a file.
- *Foundation:* ordinary paging.
- *Application:* measured across all sessions on 2026-10-18: 428 of 721 Read calls had no `limit` and pulled whole files.
- *Benefit:* TOK, cache writes.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none. The measurement is the argument.
- *Limits:* a wrong range costs a second read, and the 2026-10-11 analysis found a prompt sentence alone does not change this behaviour.

**8.5 Query planning across retrieval back ends**
- *Mechanism:* route a question to the cheapest back end that can answer it, and fall back only on failure.
- *Foundation:* query routing and federated search; cost-based planning in databases.
- *Application:* code questions to graft, markdown by meaning to `docs-search`, past decisions to `memsearch`, raw text to `rg`, and whole files last.
- *Benefit:* TOK, cache writes; LAT.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little, but measured as entirely absent: graft, docs-search and memsearch produced 0.24% of retrieval volume on 2026-10-18.
- *Limits:* every back end needs a fresh index, which is why a staleness gate matters.

**8.6 Reciprocal rank fusion of several retrievers**
- *Mechanism:* combine rankings from lexical and semantic retrievers without tuning weights.
- *Foundation:* reciprocal rank fusion, Cormack, Clarke and Buettcher.
- *Application:* fuse `rg` hits with `docs-search` and graft coupling ranks.
- *Benefit:* COR of retrieval, so fewer follow-up reads; TOK indirectly.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* avoids choosing one retriever.
- *Limits:* fusion returns more candidates, so it must be paired with a budget such as 8.2.

**8.7 Prompt and context compression before the call**
- *Mechanism:* drop low-information tokens from the context with a small model or a rule set.
- *Foundation:* LLMLingua, Jiang and colleagues; Selective Context, Li and colleagues. Both are published compression methods for prompts.
- *Application:* a proxy compresses tool output before it reaches the worker.
- *Benefit:* TOK, cache writes.
- *Difficulty:* medium.
- *Maturity:* established research with implementations; the proxy form is production-ready in third-party tools.
- *Novelty:* operates on the material, not the instruction.
- *Limits:* compression can change meaning. An exact error string must survive. Measured ceiling here: the mass is in thousands of small results, and a size rule reaches only 10% to 15% of what enters a context.

**8.8 Active learning over the fault hypothesis set**
- *Mechanism:* maintain competing hypotheses and run the cheapest test that separates the most.
- *Foundation:* active learning; sequential hypothesis testing; the same rule as a halving search.
- *Application:* this is what the project instruction already demands, "list the competing causes and run the cheapest test that separates them".
- *Benefit:* TOK, output; LAT.
- *Difficulty:* low as a prompt rule, high as machinery.
- *Maturity:* established theory, already a written rule here.
- *Novelty:* none as an idea; the gap is that nothing checks it happened.
- *Limits:* needs the hypotheses to be stated before the search.

## 9. Model routing and inference budget

**9.1 Cascade with deferral**
- *Mechanism:* try a cheap model, and escalate only when its answer fails a check.
- *Foundation:* model cascades; FrugalGPT, Chen, Zaharia and Zou 2023.
- *Application:* a Haiku or Sonnet worker attempts the task; a failed acceptance check escalates to Opus.
- *Benefit:* TOK, output and all kinds.
- *Difficulty:* medium.
- *Maturity:* established research.
- *Novelty:* colony pins one model for the whole task, so a cheap attempt is never tried.
- *Limits:* needs a cheap, reliable check. Colony has one in the acceptance criteria and the reviewer sub-agent. A failed cascade pays twice.

**9.2 Per-role model assignment**
- *Mechanism:* fix a model for each role rather than for each task.
- *Foundation:* role specialisation; reported practice is `CLAUDE_CODE_SUBAGENT_MODEL`, verified present in the 2.1.295 binary.
- *Application:* explorers on Sonnet or Haiku, implementers on Opus, reviewers on Opus 4.8. Colony already pins the reviewer and now the explorer.
- *Benefit:* TOK, output.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little. Measured output a response: 1,129 at xhigh, 875 at high, 698 at medium.
- *Limits:* the archive cannot prove the cheap model suffices, because the seven Sonnet workers drew unknown task sizes.

**9.3 Thinking-budget caps per task class**
- *Mechanism:* cap the reasoning tokens for mechanical work and raise them for design work.
- *Foundation:* inference-time compute allocation; `MAX_THINKING_TOKENS`, verified present.
- *Application:* a brief's Effort section already names a level. A cap makes it enforceable.
- *Benefit:* TOK, output. Thinking is 6.5% of weighted burn at API prices and 20.7% with cache reads at zero.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* colony pins effort but caps nothing.
- *Limits:* too low a cap costs correctness on exactly the tasks that need depth.

**9.4 Self-consistency only where it pays**
- *Mechanism:* sample several answers and take the majority, but only for questions where disagreement is likely and costly.
- *Foundation:* self-consistency, Wang and colleagues 2023.
- *Application:* reserved for a decision the Master cannot check cheaply.
- *Benefit:* COR.
- *Difficulty:* low.
- *Maturity:* established research.
- *Novelty:* the gating is the contribution; blanket self-consistency multiplies cost.
- *Limits:* strictly negative for TOK. Colony's cheaper guard is one reviewer with a 12-call cap.

**9.5 Interleaved thinking control**
- *Mechanism:* decide whether reasoning persists between tool calls.
- *Foundation:* `DISABLE_INTERLEAVED_THINKING`, verified present in the binary.
- *Application:* turn it off for mechanical sequences where each step is independent.
- *Benefit:* TOK, output and the cache reads that carry it forward.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* a direct knob on the measured 20.7% thinking line.
- *Limits:* unmeasured effect on quality. It must be tested on a task class, not switched globally.

**9.6 Early exit on a satisfied criterion**
- *Mechanism:* stop as soon as every acceptance criterion is met, rather than continuing to polish.
- *Foundation:* early exit in inference; satisficing rather than optimising.
- *Application:* the worker prompt already says PASS only if every criterion passes. The missing half is an instruction to stop once they do.
- *Benefit:* TOK, output.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* names polishing as a cost. Measured: file changes are spread evenly across a session, so late work is real work, not only polish.
- *Limits:* criteria that under-specify quality make early exit harmful.

**9.7 Distilling a recurring judgement into a classifier**
- *Mechanism:* replace a repeated model judgement with a small trained model or a rule.
- *Foundation:* distillation; the general move from a model call to a function.
- *Application:* "does this diff need a reviewer" becomes a rule over changed paths and test results.
- *Benefit:* TOK, output; LAT.
- *Difficulty:* high.
- *Maturity:* proposed adaptation.
- *Novelty:* removes the call rather than making it cheaper.
- *Limits:* needs labelled judgements. Colony's verdicts are few.

**9.8 Price-aware and weight-aware budgeting**
- *Mechanism:* budget in the units the provider actually charges or meters, not in raw tokens.
- *Foundation:* cost accounting; the measured divergence between raw and weighted counts.
- *Application:* `colony report` prices from a configured rate block already. The gap is that it reports raw tokens as the headline.
- *Benefit:* it makes every other TOK claim decidable.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* this is the instrument whose absence caused a day of work on the wrong lever.
- *Limits:* the plan-limit weights are unpublished, so the report must name its assumption rather than imply a truth.

## 10. Verification and proof-carrying artifacts

**10.1 Proof-carrying results**
- *Mechanism:* an output must carry the evidence that it is right, so the consumer checks the evidence instead of redoing the work.
- *Foundation:* proof-carrying code, Necula and Lee 1997.
- *Application:* colony already requires a PASS to cite `path:line` or a URL, and the Master confirms the cited lines.
- *Benefit:* TOK, because the Master reads a citation rather than the task; COR.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* little here, much elsewhere.
- *Limits:* a citation proves a line exists, not that it is sufficient.

**10.2 Contracts as the unit of handoff**
- *Mechanism:* pass a checkable contract rather than prose.
- *Foundation:* design by contract, Meyer; specification by example.
- *Application:* a handoff states preconditions, postconditions and the test that demonstrates them.
- *Benefit:* COR; TOK, cache writes, because a contract is shorter than a transcript.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* distinguishes this from a conversational summary, which is what a handoff degenerates into.
- *Limits:* writing a contract costs output tokens up front.

**10.3 Property-based acceptance criteria**
- *Mechanism:* state criteria as properties a generator can attack, not as examples.
- *Foundation:* property-based testing, QuickCheck, Claessen and Hughes.
- *Application:* a brief's criteria become properties, so the reviewer runs a generator instead of reading code.
- *Benefit:* COR; TOK, because checking becomes mechanical.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* moves verification from a model to a process.
- *Limits:* many tasks, such as documentation, have no generatable property.

**10.4 Differential testing between a cheap and a dear worker**
- *Mechanism:* run both and compare outputs, investigating only the differences.
- *Foundation:* differential testing, McKeeman.
- *Application:* a Sonnet and an Opus worker on the same brief; a human or a reviewer looks only where they disagree.
- *Benefit:* COR; it also measures whether the cheap model suffices.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* it is the honest answer to the open question of whether a cheaper worker is good enough.
- *Limits:* doubles the token cost of the sampled tasks. Use it as an experiment, not a policy.

**10.5 Metamorphic checks where no oracle exists**
- *Mechanism:* check a relation between outputs rather than an output against a known answer.
- *Foundation:* metamorphic testing, Chen and colleagues.
- *Application:* a refactoring task must leave behaviour unchanged, which is a metamorphic relation that a test suite can assert.
- *Benefit:* COR.
- *Difficulty:* medium.
- *Maturity:* established research with practice.
- *Novelty:* gives refactor tasks a mechanical acceptance test.
- *Limits:* only applies where a relation exists.

**10.6 Bounded review with a declared budget**
- *Mechanism:* give the reviewer a hard cap on its own work, so review cost cannot run away.
- *Foundation:* contract algorithms; resource-bounded verification.
- *Application:* colony's reviewer has a 12-call cap, chosen after unbounded reviews averaged 5M tokens.
- *Benefit:* TOK, all kinds.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* little. The measurement behind it is the useful part.
- *Limits:* a cap can cut a review short on a large diff, and nothing currently scales the cap with diff size.

**10.7 Verification-first task ordering**
- *Mechanism:* write the check before the change, so the change has a target.
- *Foundation:* test-driven development; specification-first design.
- *Application:* a brief's first criterion is a failing test the worker must make pass.
- *Benefit:* COR; TOK, by shortening the search.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none, and colony's briefs already lean this way.
- *Limits:* writing the test may need the design the task is meant to produce.

**10.8 Byzantine-tolerant aggregation of worker claims**
- *Mechanism:* treat worker outputs as possibly wrong reports and aggregate with a quorum.
- *Foundation:* Byzantine fault tolerance; Dawid and Skene for noisy labels.
- *Application:* three cheap workers vote on a classification the Master cannot check cheaply.
- *Benefit:* COR.
- *Difficulty:* high.
- *Maturity:* established theory, speculative adaptation.
- *Novelty:* applies a fault model to agent output rather than to machines.
- *Limits:* strictly negative for TOK, and correlated model errors break the independence the quorum assumes.

## 11. Provenance and epistemic state

**11.1 Reasoning dependency graph**
- *Mechanism:* record assumptions, findings and decisions as nodes, with the evidence and the dependencies as edges.
- *Foundation:* truth maintenance systems, Doyle; provenance graphs.
- *Application:* a worker's conclusion names the facts it rests on, so a later worker neither repeats nor silently contradicts it.
- *Benefit:* TOK, by preventing re-derivation; COR.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* this targets the one cost never measured here. The 2026-10-11 analysis proved file rediscovery immaterial but could prove nothing about re-derived conclusions, because re-deriving leaves no tool call.
- *Limits:* unmeasured benefit. It needs a cheap proxy measurement first, such as counting conclusions restated across tasks.

**11.2 Justification-based truth maintenance for invalidation**
- *Mechanism:* when a supporting fact changes, retract every conclusion that depended on it.
- *Foundation:* justification-based truth maintenance.
- *Application:* a commit that changes a cited line marks the dependent findings suspect.
- *Benefit:* COR.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* mechanises the staleness problem the user raised about written numbers.
- *Limits:* needs every finding to cite machine- checkable evidence, which is why 10.1 is a prerequisite.

**11.3 Provenance on every recorded number**
- *Mechanism:* a figure in a document carries the command that produces it.
- *Foundation:* provenance in scientific workflows; literate and reproducible reporting.
- *Application:* `colony.md` carries measured counts that went stale within days. A provenance pointer lets a reader re-derive instead of trusting.
- *Benefit:* COR; TOK, because documents shrink to pointers.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little; the value is the staleness it prevents.
- *Limits:* the command must stay runnable.

**11.4 Epistemic status tags on each claim**
- *Mechanism:* label every claim as measured, inferred, assumed or unknown.
- *Foundation:* epistemic modality in knowledge representation; standard practice in decision records.
- *Application:* already the house style here, and the reason this document separates confident citations from unverified ones.
- *Benefit:* COR.
- *Difficulty:* low.
- *Maturity:* production-ready, already in use.
- *Novelty:* none.
- *Limits:* labels are only as honest as the writer.

**11.5 Shared blackboard with a single writer per region**
- *Mechanism:* agents read a common structured store and write only their own region.
- *Foundation:* blackboard architectures, Hearsay-II; tuple spaces.
- *Application:* colony's state directory is a crude blackboard. A typed one would let a worker read the plan without a Master turn.
- *Benefit:* TOK, by removing Master mediation; THR.
- *Difficulty:* medium.
- *Maturity:* established architecture, proposed adaptation.
- *Novelty:* removes the Master from the read path, and the Master is the dearest request at 348,563 tokens.
- *Limits:* a shared store invites the conflicts that category 5 exists to prevent.

**11.6 Unresolved-question ledger**
- *Mechanism:* keep open questions as first-class records with an owner and a state, closed only by an answer.
- *Foundation:* issue tracking; the project's own rule that a question stays open until answered.
- *Application:* colony records QUESTION and ANSWERED events; the measured archive holds 46 questions and 61 answers.
- *Benefit:* COR.
- *Difficulty:* low.
- *Maturity:* production-ready, partly in colony.
- *Novelty:* little.
- *Limits:* a ledger nobody reads is a cost, not a control.

**11.7 Decision records as the unit of memory**
- *Mechanism:* store the decision and its reasons, not the conversation.
- *Foundation:* architecture decision records.
- *Application:* colony's decision table in `colony.md` and the `Claude-System` decision records.
- *Benefit:* TOK, cache writes, because a successor reads a decision rather than a transcript; COR.
- *Difficulty:* low.
- *Maturity:* production-ready, already in use.
- *Novelty:* none.
- *Limits:* records go stale, which 11.2 and 11.3 address.

**11.8 Semantic recall over past sessions**
- *Mechanism:* retrieve past notes by meaning, so a session reads only what bears on its task.
- *Foundation:* dense retrieval over a local index; `memsearch` and `docs-search` implement it here.
- *Application:* a worker recalls prior decisions before re-deriving them.
- *Benefit:* TOK, by preventing re-derivation.
- *Difficulty:* low, since both tools exist and are indexed.
- *Maturity:* production-ready.
- *Novelty:* none, and the gap is measured: these tools produced 0.24% of retrieval volume over three days.
- *Limits:* recall of a wrong past decision is worse than none, so a recalled claim still needs its source checked.

## 12. Fault tolerance and recovery

**12.1 Checkpointing with a recovery contract**
- *Mechanism:* write enough state at intervals that a fresh process can resume.
- *Foundation:* checkpoint and restart; write-ahead logging.
- *Application:* colony's Master checkpoint and `colony restart` already do this, with a 15-minute freshness rule.
- *Benefit:* COR; TOK, because a restart does not replay the conversation.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* little.
- *Limits:* a worker has no checkpoint. A worker lost mid-task loses everything it spent.

**12.2 Idempotent task definition**
- *Mechanism:* define a task so that running it twice is harmless.
- *Foundation:* idempotence in distributed systems.
- *Application:* a brief states the end state, not the steps, so a retry converges rather than duplicating.
- *Benefit:* COR; TOK on retries.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little, and colony's goal-shaped briefs already lean this way.
- *Limits:* a task that appends, such as adding a changelog line, is not naturally idempotent.

**12.3 Supervision trees for worker failure**
- *Mechanism:* a supervisor restarts a failed child under a declared strategy, rather than a coordinator reasoning about it.
- *Foundation:* supervision trees in Erlang and OTP.
- *Application:* `colony wait` detects IDLE and FAIL; a strategy table would decide restart, re-brief or escalate without a Master turn.
- *Benefit:* TOK, because the Master does not reason about each failure; THR.
- *Difficulty:* medium.
- *Maturity:* production-ready in Erlang, proposed adaptation.
- *Novelty:* makes recovery declarative.
- *Limits:* a wrong automatic restart burns a full worker's budget. Measured: all 10 archived IDLE events were false, which is why IDLE now needs three consecutive checks.

**12.4 Circuit breaker on a failing task class**
- *Mechanism:* stop attempting a class of work after repeated failures, until something changes.
- *Foundation:* the circuit breaker pattern, Nygard.
- *Application:* three FAILs on the same brief stop the retries and raise a question instead.
- *Benefit:* TOK.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little. Measured basis: five reruns in the 2026-10-11 archive cost about 77.9M tokens, 2.2% of the total then.
- *Limits:* needs a definition of "same class" that `retry_of` can supply.

**12.5 Bulkheads between task groups**
- *Mechanism:* isolate resources so one runaway task cannot starve the rest.
- *Foundation:* the bulkhead pattern.
- *Application:* a per-task share of the usage window, so one worker cannot exhaust the five-hour quota.
- *Benefit:* THR; COR.
- *Difficulty:* medium.
- *Maturity:* production-ready pattern, proposed adaptation.
- *Novelty:* applies a resource-isolation pattern to a shared token quota.
- *Limits:* needs a readable quota and a mid-turn enforcement point. Colony records that a worker's usage appears only at a turn boundary, so a ceiling cannot be enforced inside a turn.

**12.6 Compensating actions for landed partial work**
- *Mechanism:* record how to undo each landed step.
- *Foundation:* sagas, as in 5.6, applied to recovery rather than scheduling.
- *Application:* a worktree branch is itself the compensation: discard it.
- *Benefit:* COR.
- *Difficulty:* low with worktrees.
- *Maturity:* production-ready.
- *Novelty:* the distinction from 5.6 is that this is the recovery mechanism, not the scheduling model.
- *Limits:* work merged to the main branch needs a real revert.

**12.7 Fail-fast gates before expensive work**
- *Mechanism:* check the cheap preconditions before spending.
- *Foundation:* fail-fast design.
- *Application:* `colony start` now refuses a missing or stale graft index before a worker starts, which costs one subprocess instead of a session.
- *Benefit:* TOK; COR.
- *Difficulty:* low.
- *Maturity:* production-ready, shipped 2026-10-19.
- *Novelty:* little.
- *Limits:* a strict gate blocks work. Measured: two of the live colony roots currently fail the gate until `graft build` runs.

**12.8 Deadline propagation through the task graph**
- *Mechanism:* pass a deadline down, so a sub-task knows how long it may take.
- *Foundation:* deadline propagation in RPC systems; context deadlines.
- *Application:* a reviewer sub-agent inherits the remaining budget rather than a fixed 12 calls.
- *Benefit:* LAT; TOK.
- *Difficulty:* medium.
- *Maturity:* production-ready in RPC, proposed adaptation.
- *Novelty:* makes a nested bound relative, not absolute.
- *Limits:* needs a budget the sub-agent can read.

## 13. Query-optimisation analogues

**13.1 Cost-based planning of a task graph**
- *Mechanism:* enumerate candidate plans and choose by an estimated cost model.
- *Foundation:* System R's dynamic-programming optimiser, Selinger and colleagues 1979.
- *Application:* the Master compares a one-worker plan against a three-worker plan using measured cost per request by role.
- *Benefit:* TOK; LAT.
- *Difficulty:* high.
- *Maturity:* production-ready in databases, proposed adaptation.
- *Novelty:* makes plan choice an optimisation over a measured cost model rather than a judgement.
- *Limits:* needs cardinality estimates. Colony's closest estimate is the length-to-cost fit, `16,474 x responses ** 1.50`, at R squared 0.81.

**13.2 Cardinality estimation for task size**
- *Mechanism:* predict the size of a task before running it.
- *Foundation:* cardinality estimation and its known failure modes in query optimisation.
- *Application:* predict a brief's request count from its criteria count and input count.
- *Benefit:* it makes 13.1 and all budgeting possible.
- *Difficulty:* high.
- *Maturity:* established problem, proposed adaptation.
- *Novelty:* names the weakest link in every budget scheme.
- *Limits:* measured as hard here. At equal criteria counts the archive shows a 3.2 to 10.6 times spread in response counts, so criteria count is a poor predictor.

**13.3 Predicate pushdown into the tool**
- *Mechanism:* apply the filter at the data source, not after the data arrives.
- *Foundation:* predicate pushdown.
- *Application:* `rg -n pattern file` instead of `cat file` followed by model reading. Measured: `grep`-class calls average 207 result tokens against 4,902 for a Read.
- *Benefit:* TOK, cache writes.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none; it is the single clearest retrieval win in the measurements.
- *Limits:* a filter needs the pattern, which needs a prior guess.

**13.4 Semi-join reduction before a join**
- *Mechanism:* ship the small key set first and fetch only the matching rows.
- *Foundation:* semi-join reduction, Bernstein and Chiu.
- *Application:* get the symbol list from graft, then read only the spans that match.
- *Benefit:* TOK, cache writes.
- *Difficulty:* low.
- *Maturity:* production-ready concept, proposed adaptation.
- *Novelty:* names the two-step retrieval pattern precisely.
- *Limits:* two round trips, so two requests. It wins when the payload saving exceeds one request's context.

**13.5 Sideways information passing between workers**
- *Mechanism:* a running task's discovered filter is passed to a sibling to prune its work.
- *Foundation:* sideways information passing in query execution.
- *Application:* one worker finds the migration is only on three tables, and the sibling writing tests learns it.
- *Benefit:* TOK; LAT.
- *Difficulty:* high, because it needs a live channel.
- *Maturity:* established in databases, speculative adaptation.
- *Novelty:* mid-flight pruning, which a plan-then-run orchestrator cannot do.
- *Limits:* colony has a message path through the Master, whose request is the dearest in the system.

**13.6 Adaptive query re-planning**
- *Mechanism:* change the plan when the observed data contradicts the estimate.
- *Foundation:* adaptive query processing; eddies, Avnur and Hellerstein.
- *Application:* a task that passes twice its predicted size triggers a re-plan rather than a hard stop.
- *Benefit:* TOK; COR.
- *Difficulty:* high.
- *Maturity:* established research, proposed adaptation.
- *Novelty:* this is the safe form of the budget that was reverted. A re-plan reconsiders the decomposition; a hard ceiling simply cuts.
- *Limits:* needs 13.2, which is measured as weak.

**13.7 Caching a plan, not only a result**
- *Mechanism:* reuse the plan shape for a recurring request class.
- *Foundation:* plan caching in databases.
- *Application:* "add an endpoint" always decomposes the same way, so the Master loads the shape instead of deriving it.
- *Benefit:* TOK, because the Master spends a median 6.6M tokens between task starts.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* targets the Master, which is 36% of spend and usually ignored.
- *Limits:* a cached plan applied to an unlike request is worse than none. Databases hit this as parameter-sensitive plans.

**13.8 Statistics collection to feed the optimiser**
- *Mechanism:* keep histograms of past executions to improve estimates.
- *Foundation:* table statistics in query optimisers.
- *Application:* `colony report` plus the archive already hold the raw material: requests, tool calls, span, verdict, model and effort for 243 sessions.
- *Benefit:* it is the prerequisite for 13.1, 13.2 and all of category 7.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little, and it is the measured bottleneck: the statistics are kept in raw tokens, which is the wrong unit.

## 14. Distributed-systems protocols

**14.1 Lease-based task ownership**
- *Mechanism:* a worker holds a time-bounded lease on its task, renewed while alive and reclaimable when it lapses.
- *Foundation:* leases, Gray and Cheriton.
- *Application:* replaces colony's IDLE heuristic, which had no true positive in 10 archived events.
- *Benefit:* COR; THR.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* a lapse is a fact, where IDLE was an inference from pane state.
- *Limits:* a worker paused inside a long tool call must still renew, which needs a heartbeat the agent does not emit by itself.

**14.2 Event sourcing with a derived read model**
- *Mechanism:* the event log is the truth, and every view is derived from it.
- *Foundation:* event sourcing and CQRS.
- *Application:* colony already appends STARTED, PASS, FAIL, QUESTION, ANSWERED, IDLE and CLOSED, and `tests/archive-check.py` reconciles the derived view against the archive.
- *Benefit:* COR.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* none.
- *Limits:* two colonies listing the same Master session already happened, which `archive-check.py` now reconciles.

**14.3 Vector clocks for cross-worker causality**
- *Mechanism:* track what each worker knew when, so concurrent and causal updates can be told apart.
- *Foundation:* vector clocks, Fidge and Mattern; Lamport clocks for the total-order case.
- *Application:* decide whether a worker's conclusion predates a sibling's change.
- *Benefit:* COR.
- *Difficulty:* medium.
- *Maturity:* production-ready, speculative for agents.
- *Novelty:* gives "was this decision stale" a definite answer.
- *Limits:* heavier than the revision and dirty pair colony records, and useful only once workers interact.

**14.4 Quorum reads for a shared fact**
- *Mechanism:* read from enough replicas to be sure of the current value.
- *Foundation:* quorum systems; Dynamo-style consistency.
- *Application:* not applicable to a single Git tree, which has one replica.
- *Benefit:* none here.
- *Difficulty:* n/a.
- *Maturity:* production-ready elsewhere.
- *Novelty:* its inapplicability is the useful finding: colony has one source of truth, so quorum machinery is pure overhead.
- *Limits:* listed to rule it out, as the brief asks.

**14.5 Gossip for state propagation among workers**
- *Mechanism:* peers exchange state directly rather than through a coordinator.
- *Foundation:* epidemic and gossip protocols, Demers and colleagues.
- *Application:* workers share findings peer to peer, bypassing the Master.
- *Benefit:* TOK, because the Master's request costs 348,563 tokens; LAT.
- *Difficulty:* high.
- *Maturity:* production-ready in infrastructure, speculative for agents.
- *Novelty:* removes the coordinator from the information path.
- *Limits:* colony's rule of one reader exists to prevent exactly this. Gossip would need a message protocol and would reintroduce ordering problems.

**14.6 Two-phase commit for a coordinated landing**
- *Mechanism:* all participants prepare, then all commit or all abort.
- *Foundation:* two-phase commit, Gray.
- *Application:* two workers changing both sides of a contract land together or not at all.
- *Benefit:* COR.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* gives gang scheduling, 4.7, a commit protocol.
- *Limits:* blocking. A coordinator failure leaves participants stuck, which is why sagas, 5.6, are often preferred.

**14.7 Idempotent message delivery for worker instructions**
- *Mechanism:* give each instruction an id so a redelivery is harmless.
- *Foundation:* at-least-once delivery with idempotence keys.
- *Application:* `colony answer` sends a reply to a worker's pane. A duplicate send today would be a second prompt.
- *Benefit:* COR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little.
- *Limits:* a prompt is natural language, so the worker, not the protocol, must notice the duplicate.

**14.8 Backpressure-aware fan-out limits**
- *Mechanism:* bound concurrency by what the slowest stage can absorb.
- *Foundation:* flow control; the same principle as 4.6 applied at admission.
- *Application:* cap open workers by the review capacity, not by pane count.
- *Benefit:* THR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* the distinction from 4.6 is that this sets a static ceiling at admission, while 4.6 adjusts dynamically from the queue.
- *Limits:* needs review throughput measured. Colony does not measure it.

## 15. Synthesis, partial evaluation and workflow extraction

**15.1 Deterministic workflow extraction**
- *Mechanism:* turn a repeated, successful agent procedure into a script or tool.
- *Foundation:* partial evaluation; the Futamura projections, Futamura 1971, for the theory of specialising an interpreter.
- *Application:* measured basis: 24,187 Bash calls across 227 workers, 78.2% beginning with `cd`, at a median 274 characters. Repeated shapes become scripts.
- *Benefit:* TOK, output and cache writes; LAT, because a removed call is a removed request.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* the unit removed is a model call, not a token.
- *Limits:* needs trace mining to find the shapes, and a script rots when the repository moves.

**15.2 Program synthesis for the mechanical part**
- *Mechanism:* derive code from a specification with a solver rather than a model.
- *Foundation:* program synthesis; syntax-guided synthesis.
- *Application:* generating a boilerplate adapter from a schema.
- *Benefit:* TOK, output; COR, because synthesis is sound.
- *Difficulty:* high.
- *Maturity:* established research.
- *Novelty:* replaces the model entirely where the specification is formal.
- *Limits:* only applies to narrow, formalisable tasks.

**15.3 Automated program repair for test failures**
- *Mechanism:* search for a patch that makes a failing test pass.
- *Foundation:* automated program repair; GenProg, Le Goues and colleagues.
- *Application:* a cheap first attempt at a failing test before a model is asked.
- *Benefit:* TOK, output; LAT.
- *Difficulty:* high.
- *Maturity:* established research with limited practice.
- *Novelty:* uses the test as the oracle rather than the model's judgement.
- *Limits:* repairs often overfit the test, which is a known weakness.

**15.4 Macro extraction for prompt sequences**
- *Mechanism:* name a recurring multi-step instruction once and reference it.
- *Foundation:* macro expansion; procedural abstraction.
- *Application:* colony's `PROMPT` already does this for the worker lifecycle. Skills do it for procedures.
- *Benefit:* TOK, cache reads, since a reference is shorter than a recital.
- *Difficulty:* low.
- *Maturity:* production-ready, already in use.
- *Novelty:* none.
- *Limits:* a macro the model must still expand into reasoning saves only the restatement.

**15.5 Specialising the agent to the repository**
- *Mechanism:* bake stable repository facts into the standing instruction, so they are never rediscovered.
- *Foundation:* partial evaluation again, with the repository as the static input.
- *Application:* `CLAUDE.md` is exactly this.
- *Benefit:* TOK, cache writes, at the cost of cache reads, since the standing text rides every request.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* names the trade-off precisely: a standing fact is paid once per request, a rediscovered fact once per task. The crossover depends on the unmeasured cache-read weight.
- *Limits:* measured size here: workers start at a median 44,592 tokens, and `/context` can now break that down without a probe.

**15.6 Tool synthesis on demand**
- *Mechanism:* let the agent write a tool, then reuse it instead of repeating the reasoning.
- *Foundation:* the general tool-making idea in agent research; labelled emerging.
- *Application:* a worker writes `bin/check-contract.sh` and later workers run it.
- *Benefit:* TOK; LAT.
- *Difficulty:* medium.
- *Maturity:* emerging research.
- *Novelty:* converts a repeated judgement into a repository artifact.
- *Limits:* an unreviewed tool is a liability, and it must be owned by the project, not by a worker.

**15.7 Compiling the brief into a checklist the runtime enforces**
- *Mechanism:* turn a specification into machine-checked steps rather than instructions to be obeyed.
- *Foundation:* workflow engines; the general move from instruction to enforcement.
- *Application:* a probe on 2026-10-19 showed a PreToolUse hook can inject context and deny a call. So enforcement is available to a hosted agent.
- *Benefit:* COR; TOK where it blocks waste.
- *Difficulty:* medium.
- *Maturity:* production-ready mechanism, proposed adaptation.
- *Novelty:* enforcement does not rely on the model's compliance, which the 2026-10-11 analysis found unreliable for a prompt sentence.
- *Limits:* a hook that denies wrongly can deadlock a worker. Whether a per-session `--settings` hook merges with existing hooks is unverified here.

**15.8 Inline caching of a repeated decision site**
- *Mechanism:* remember the answer at the exact point it was needed, and check a cheap guard before reusing it.
- *Foundation:* polymorphic inline caches, Holzle, Chambers and Ungar.
- *Application:* "which test command does this project use" is answered once per repository and guarded by the lockfile hash.
- *Benefit:* TOK; LAT.
- *Difficulty:* medium.
- *Maturity:* established in runtimes, proposed adaptation.
- *Novelty:* the guard is what separates this from a plain cache, as in 2.5.
- *Limits:* the guard must be cheaper than the answer.

## 16. Inference and serving layer

This category is split deliberately. A hosted Claude Code client controls none
of the serving stack. The entries say which side of that line they fall on.

**16.1 Prefix caching at the serving layer**
- *Mechanism:* reuse the key-value state of a shared prompt prefix across requests.
- *Foundation:* radix-tree prefix caching as in SGLang's RadixAttention, Zheng and colleagues; paged attention as in vLLM, Kwon and colleagues 2023.
- *Application:* a hosted client benefits from it but cannot configure it. The only lever is prefix stability, as in 1.6.
- *Benefit:* LAT, and a billing discount on resent tokens.
- *Difficulty:* low for the client, since it is automatic.
- *Maturity:* production-ready.
- *Novelty:* none.
- *Limits:* measured here as already saturated: fresh input is 0.0010% of worker input. There is no headroom left to win.

**16.2 Key-value cache sharing across agents**
- *Mechanism:* share attention state between agents that hold overlapping context, rather than resending tokens.
- *Foundation:* an active research area in model serving.
- *Application:* not available. It requires control of the serving infrastructure, which a hosted session does not expose.
- *Benefit:* LAT and serving compute, not billed input tokens.
- *Difficulty:* research-level.
- *Maturity:* emerging research.
- *Novelty:* high, and irrelevant to us.
- *Limits:* listed to close it off. Reuse of attention state does not by itself reduce billed input tokens, so even with access the TOK claim would need separate proof.

**16.3 Speculative decoding**
- *Mechanism:* a small model drafts tokens that a large model verifies in parallel.
- *Foundation:* speculative decoding, Leviathan, Kalman and Matias 2023.
- *Application:* none for a hosted client; it is a provider-side optimisation.
- *Benefit:* LAT and serving compute.
- *Difficulty:* n/a for us.
- *Maturity:* production-ready at providers.
- *Novelty:* none for us.
- *Limits:* it changes neither token counts nor our bill.

**16.4 Continuous batching**
- *Mechanism:* admit new requests into a running batch instead of waiting for it to finish.
- *Foundation:* continuous batching, as in Orca, Yu and colleagues 2022.
- *Application:* provider-side. It explains why our measured latency flattens above a 100k context, at 7.5 to 8.1 seconds, rather than rising with size.
- *Benefit:* LAT.
- *Difficulty:* n/a.
- *Maturity:* production-ready.
- *Novelty:* the useful part is the inference: our latency is queue-dominated above 100k, so shrinking a 200k context to 150k buys little time.
- *Limits:* an inference from our own latency table, not a provider statement.

**16.5 Separating billing from compute in every claim**
- *Mechanism:* keep three quantities apart: billed tokens, serving compute, and wall-clock latency.
- *Foundation:* basic cost accounting, and the explicit caution that cache reuse does not reduce billed input.
- *Application:* every TOK claim in this document names the token kind it moves.
- *Benefit:* it prevents a whole class of false saving.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* it is the discipline whose absence cost a day here.
- *Limits:* needs the provider's weighting, which for plan limits is unpublished.

**16.6 Request shaping to the cache's time to live**
- *Mechanism:* pace requests so a cached prefix is still warm when the next one arrives.
- *Foundation:* cache time-to-live management.
- *Application:* this session runs on a one-hour prompt-cache lifetime. Ordinary pacing keeps the prefix warm, so scheduling extra requests to warm it is waste.
- *Benefit:* TOK, cache writes avoided on a miss; LAT.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* it mostly rules out a tempting mistake.
- *Limits:* the lifetime is a provider setting that can change.

**16.7 Model-size routing as an inference-budget decision**
- *Mechanism:* treat model choice as allocating inference compute to a task.
- *Foundation:* the same family as cascades, 9.1, but framed at the system level.
- *Application:* colony's policy file pins a model and an effort for each brief.
- *Benefit:* TOK, output.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* the distinction from 9.1 is that 9.1 escalates within a task, while this assigns before it starts.
- *Limits:* a wrong assignment is discovered only at the end, which is what makes 9.1 the stronger form.

**16.8 Measuring the host's own accounting**
- *Mechanism:* read the tool's own usage and context reports rather than inferring them.
- *Foundation:* instrumentation over inference.
- *Application:* `/usage` and `/context` are verified present in Claude Code 2.1.295. `/context` breaks down the system prompt, instructions, servers and skills.
- *Benefit:* it measures the 44,592-token startup context directly.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* none, and I reached for a probe before checking the host had the answer.
- *Limits:* these are interactive commands, so they are awkward to log automatically.

## 17. Control theory and adaptive allocation

**17.1 Closed-loop concurrency control**
- *Mechanism:* adjust the worker count from a measured error signal rather than a fixed setting.
- *Foundation:* feedback control; a PID loop in the simplest form.
- *Application:* raise concurrency while the review queue is short and the quota is ample, and lower it otherwise.
- *Benefit:* THR; COR.
- *Difficulty:* medium.
- *Maturity:* production-ready in autoscaling, proposed adaptation.
- *Novelty:* replaces a fixed rule. Measured: mean concurrency is 1.96 against a maximum seen of 6, so the loop has headroom.
- *Limits:* needs a stable measurement interval. Worker spans vary from 30 to 64 minutes between median and mean.

**17.2 Additive-increase, multiplicative-decrease on concurrency**
- *Mechanism:* add a worker on success, halve on a conflict or a quota warning.
- *Foundation:* AIMD congestion control, as in TCP.
- *Application:* a merge conflict or an abort halves the fan-out.
- *Benefit:* THR; COR.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* a known-stable control law, where an ad hoc rule may oscillate.
- *Limits:* AIMD assumes a congestion signal. Conflicts here are rare, since concurrency is 1.96.

**17.3 Token bucket for a shared quota**
- *Mechanism:* meter spending against a refilling bucket.
- *Foundation:* the token bucket algorithm.
- *Application:* pace work against the five-hour window, so a long task is not started with an empty bucket.
- *Benefit:* COR, by avoiding a stopped task; THR.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* little.
- *Limits:* needs the bucket's units. The weight of each token kind against the window is unpublished, so the bucket cannot be filled accurately.

**17.4 Lyapunov-stable work admission**
- *Mechanism:* admit work only while a stability function decreases, guaranteeing the queue does not grow without bound.
- *Foundation:* Lyapunov drift and backpressure, Tassiulas and Ephremides 1992.
- *Application:* the formal version of 4.6.
- *Benefit:* THR.
- *Difficulty:* research-level.
- *Maturity:* established theory, speculative adaptation.
- *Novelty:* offers a stability proof rather than a heuristic.
- *Limits:* heavy machinery for a system running two workers at a time.

**17.5 Set-point control on context size**
- *Mechanism:* hold each session's context near a target by compacting or handing over before it drifts above.
- *Foundation:* set-point control; the same idea as a high-water mark.
- *Application:* the auto-compact window is a crude set point, now 400k for every session including Masters.
- *Benefit:* LAT, since latency is 3.9 seconds under a 50k context against 8.1 at 200k; TOK, cache reads.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* little.
- *Limits:* the TOK part rests on cache reads, whose weight is unknown. The LAT part is measured and real, but flattens above 100k.

**17.6 Rate limiting the Master, not the workers**
- *Mechanism:* constrain the dearest component first.
- *Foundation:* bottleneck analysis; Amdahl's law for the limit on any local fix.
- *Application:* Masters are 36% of spend at 348,563 tokens a request. A Master that reasons less per task start would save more than any worker change.
- *Benefit:* TOK, all kinds.
- *Difficulty:* medium.
- *Maturity:* proposed adaptation.
- *Novelty:* inverts the usual focus on workers, which are 59% of spend but far more numerous.
- *Limits:* the Master's work is coordination; cutting it needs the deterministic runtime of 3.1.

**17.7 Hysteresis to prevent thrashing**
- *Mechanism:* use different thresholds for starting and stopping, so a control loop does not oscillate.
- *Foundation:* hysteresis in control systems.
- *Application:* colony's IDLE detection now needs three consecutive checks plus a pane that is not waiting, after all 10 archived IDLE events proved premature.
- *Benefit:* COR; TOK, by not waking the Master for nothing.
- *Difficulty:* low.
- *Maturity:* production-ready, already in colony.
- *Novelty:* none, and the measurement is the point: a Master response in that archive had a median cost of 319,000 tokens.
- *Limits:* hysteresis delays a true detection.

**17.8 Queue-length-based autoscaling of worker count**
- *Mechanism:* scale workers from the depth of the ready queue.
- *Foundation:* queue-based autoscaling.
- *Application:* ready tasks in the DAG of 3.1 drive how many panes or tabs open.
- *Benefit:* THR; LAT.
- *Difficulty:* medium.
- *Maturity:* production-ready.
- *Novelty:* the distinction from 17.1 is the signal: queue depth rather than an error term.
- *Limits:* parallel sessions share one quota, so scaling up costs tokens proportionally and buys only time.

## 18. Measurement and causal inference

**18.1 Token attribution per task and per role**
- *Mechanism:* count input, output, each cache kind, tool calls, retries and duplicate work for every task and worker.
- *Foundation:* cost accounting; profiling.
- *Application:* `colony report` counts the categories and prices them from a configured rate block. The defect is that raw tokens are the headline.
- *Benefit:* it decides every other entry in this document.
- *Difficulty:* low.
- *Maturity:* production-ready, partly in colony.
- *Novelty:* none as an idea. Its absence in weighted units caused a day of work on a lever that may weigh zero.
- *Limits:* the weights are unpublished, so the report must name its assumption.

**18.2 Tokens per verified, integrated change**
- *Mechanism:* make the denominator a change that was accepted and merged, not a session.
- *Foundation:* throughput accounting; the general rule against proxy metrics.
- *Application:* `verdict` and `retry_of` exist from 2026-10-11, so the metric is computable from then on.
- *Benefit:* COR of the whole optimisation effort.
- *Difficulty:* low.
- *Maturity:* production-ready.
- *Novelty:* it counts rework into the cost, which a per-session count hides.
- *Limits:* no task before 2026-10-11 can be counted, and the verdict is a human judgement, so the metric is sparse.

**18.3 Paired experiments with variance reduction**
- *Mechanism:* compare two policies on matched tasks, and use a pre-period covariate to cut variance.
- *Foundation:* paired designs; CUPED, Deng, Xu, Kohavi and Walker 2013.
- *Application:* compare a routing change on matched briefs, using predicted size as the covariate.
- *Benefit:* it makes a real effect detectable in fewer runs.
- *Difficulty:* medium.
- *Maturity:* production-ready in experimentation.
- *Novelty:* it answers a measured problem. A controlled proof of an 8% effect needs about 49 paired runs and near a billion tokens.
- *Limits:* needs a covariate that predicts cost. Measured: criteria count is a poor one, with a 3.2 to 10.6 times spread at equal counts.

**18.4 Switchback designs over time**
- *Mechanism:* alternate the policy in time blocks, so each period is its own control.
- *Foundation:* switchback and crossover designs.
- *Application:* run one policy in the morning and the other in the afternoon, repeatedly.
- *Benefit:* it controls for drift in task mix and in provider behaviour.
- *Difficulty:* medium.
- *Maturity:* established practice.
- *Novelty:* it handles a real hazard here: the archive shows the token-to-meter ratio changing over time.
- *Limits:* needs enough blocks. Colony completes a handful of tasks a day.

**18.5 Always-valid sequential testing**
- *Mechanism:* monitor an experiment continuously without inflating the false-positive rate.
- *Foundation:* sequential testing with always-valid p-values.
- *Application:* stop an orchestration experiment as soon as the evidence is sufficient, rather than at a fixed run count.
- *Benefit:* TOK, because fewer runs are needed.
- *Difficulty:* medium.
- *Maturity:* established research with practice.
- *Novelty:* it fits a setting where each sample costs millions of tokens.
- *Limits:* it still needs an effect large enough to see.

**18.6 Off-policy estimation from the archive**
- *Mechanism:* estimate a new policy's value from logged behaviour instead of running it.
- *Foundation:* inverse propensity scoring and doubly robust estimators, as in 7.3.
- *Application:* the 243 archived sessions are the log.
- *Benefit:* TOK, since the estimate costs no model calls.
- *Difficulty:* high.
- *Maturity:* established research.
- *Novelty:* the distinction from 7.3 is purpose: 7.3 evaluates a policy to learn one, this evaluates a policy to decide whether to ship it.
- *Limits:* past choices were deterministic, so propensities are unknown and the estimate is biased by an unknown amount.

**18.7 Shapley attribution of cost across a task chain**
- *Mechanism:* divide total cost among the contributing steps by their marginal contribution.
- *Foundation:* the Shapley value.
- *Application:* in a chain of a plan, three workers and a review, assign the cost of the final accepted change fairly.
- *Benefit:* it finds which stage to optimise.
- *Difficulty:* high.
- *Maturity:* established theory, proposed adaptation.
- *Novelty:* it avoids blaming the last step for the whole chain's cost.
- *Limits:* exponential in the number of steps without sampling.

**18.8 Negative-result logging**
- *Mechanism:* record what was tried and failed, with the number, so nobody retries it.
- *Foundation:* ordinary scientific practice; the project's own decision tables.
- *Application:* this repository already does it. The 2026-10-11 closure of a tool-output size rule, and the 2026-10-19 reversal of a request budget, are both recorded with their measurements.
- *Benefit:* TOK, by preventing repeated work at the human level; COR.
- *Difficulty:* low.
- *Maturity:* production-ready, already in use.
- *Novelty:* none.
- *Limits:* only works if the record is read before the retry.

## Synthesis

### Top 15 by expected impact, with the condition attached

Leverage here depends on the cache-read weight, which is unmeasured. So each
entry states the condition under which it is worth doing.

| Rank | Concept | Objective | Holds when |
|---|---|---|---|
| 1 | 18.1 token attribution in weighted units | decides the rest | always |
| 2 | 18.2 tokens per verified change | decides the rest | always |
| 3 | 9.3 thinking-budget caps | TOK, output | always; output is 15.2% to 48% of weighted burn |
| 4 | 9.2 per-role model assignment | TOK, output | always |
| 5 | 13.3 predicate pushdown into the tool | TOK, cache writes | always; 207 tokens a call against 4,902 |
| 6 | 8.3 signature-level retrieval first | TOK, cache writes | always; measured at zero use today |
| 7 | 8.5 query planning across back ends | TOK, cache writes | always; 0.24% of retrieval volume today |
| 8 | 5.1 ownership partitioning | COR, THR | always; the fault is unmet, not absent |
| 9 | 5.4 worktrees for isolation | COR, THR | when concurrency rises above about three |
| 10 | 9.1 cascade with deferral | TOK, output | when a cheap acceptance check exists |
| 11 | 3.1 typed task DAG with a runtime | TOK, THR | when task shapes repeat |
| 12 | 15.1 deterministic workflow extraction | TOK, LAT | when traces show repeated shapes |
| 13 | 13.7 caching the plan, not the result | TOK | targets the Master, 36% of spend |
| 14 | 8.4 range reads with a limit | TOK, cache writes | always; 428 of 721 reads pulled whole files |
| 15 | 6.7 anytime results | COR | before any budget or ceiling is reintroduced |

Deliberately absent from this list: anything whose only effect is on cache
reads, including 1.8, 17.5 and the reverted request budget. They may be worth a
great deal. Until the weight is measured, they cannot be ranked.

### Top 10 least obvious

1. **16.4, continuous batching as an explanation.** Our latency flattens above a
   100k context, so shrinking a 200k context buys little time. That reverses the
   intuition that smaller context is always faster.
2. **6.7, anytime results.** The property that made the reverted budget unsafe
   was not its number but that a cut worker left nothing usable.
3. **17.6, rate limiting the Master.** Workers are 59% of spend and get all the
   attention; the Master's single request is the dearest in the system.
4. **13.2, cardinality estimation.** Every budget scheme silently depends on
   predicting task size, which is measured here as weak.
5. **2.1, the low hit rate of a fingerprint cache.** Cross-task refetch is 0.16%
   of material, so an action cache has almost nothing to hit.
6. **18.6, off-policy estimation.** The archive can test a policy without
   running it, which matters when a controlled run costs near a billion tokens.
7. **14.4, quorum reads being inapplicable.** One Git tree means one replica, so
   a standard distributed-systems answer is pure overhead here.
8. **16.2, cache sharing not reducing the bill.** Reusing attention state cuts
   serving compute and latency, not billed input tokens.
9. **15.5, the standing-instruction trade-off.** A fact in `CLAUDE.md` is paid
   once per request; rediscovered, once per task. Which is cheaper depends on
   the unmeasured weight.
10. **11.1, the unmeasurable cost.** Re-derived reasoning leaves no tool call,
    so the one cost nobody can count may be the largest.

### Top 10 research frontier

1. 1.1 rate-distortion bound on a brief, with the curve fitted per repository.
2. 1.2 information bottleneck for handoff fields.
3. 11.1 reasoning dependency graphs with 11.2 justification-based retraction.
4. 6.3 Gittins-index allocation across partly done tasks.
5. 6.6 knowledge-gradient allocation between rival approaches.
6. 13.6 adaptive re-planning driven by a size estimate that proved wrong.
7. 7.5 meta-learning the decomposition policy.
8. 5.7 semantic merge, which would remove the one-writer constraint.
9. 13.5 sideways information passing between live workers.
10. 18.7 Shapley attribution across a task chain.

### Dependency map

```
18.1 weighted attribution
  ├── 18.2 tokens per verified change
  │     ├── 7.1 contextual bandits        (needs a labelled objective)
  │     ├── 7.4 reward on verified change
  │     └── 18.3 paired experiments ── 18.5 sequential testing
  ├── 13.8 statistics collection
  │     ├── 13.2 cardinality estimation
  │     │     ├── 13.1 cost-based planning
  │     │     ├── 13.6 adaptive re-planning
  │     │     └── 17.3 token bucket
  │     └── 18.6 off-policy estimation
  └── every TOK claim in categories 1, 2, 8, 9, 16

10.1 proof-carrying results
  ├── 10.2 contracts as handoff
  ├── 11.2 justification-based retraction
  └── 5.8 read-set recording ── 5.2 optimistic concurrency

3.1 typed task DAG
  ├── 4.1 critical-path scheduling
  ├── 4.2 earliest-finish assignment
  ├── 13.7 plan caching
  ├── 12.3 supervision trees
  └── 17.8 queue-based autoscaling

5.1 ownership partitioning
  ├── 5.4 worktrees
  │     └── 5.3 single committer with a fencing token
  ├── 3.3 blast-radius bounding
  └── 4.7 gang scheduling ── 14.6 two-phase commit

2.7 materialised views (graft, docs-search, memsearch)
  ├── 8.3 signature-first retrieval
  ├── 8.5 query planning
  ├── 1.5 context compilation
  └── 2.2 self-adjusting computation ── 2.3 Merkle-keyed artifacts

15.1 workflow extraction ── 15.8 inline caching with a guard
6.7 anytime results ── 6.2 optimal stopping ── 9.6 early exit
```

Read it as: a parent must exist before a child is worth building. The whole left
column rests on 18.1, which is why it comes first.

### Phased roadmap

| Phase | Work | Concepts | Why now |
|---|---|---|---|
| 0 | Weighted attribution and the verified-change metric in `colony report` | 18.1, 18.2, 13.8 | Without it every later claim is unprovable, as 2026-10-19 showed |
| 1 | Output and thinking: caps, per-role models, early exit | 9.2, 9.3, 9.5, 9.6 | Survives every weighting. Low difficulty, verified knobs |
| 2 | Retrieval: pushdown, signature-first, back-end routing, range reads | 13.3, 8.3, 8.4, 8.5 | Measured at near-zero adoption. Cuts what enters a context |
| 3 | Isolation: ownership, worktrees, one committer, blast-radius admission | 5.1, 5.3, 5.4, 3.3 | Unblocks concurrency past three workers and closes a real fault |
| 4 | Master-side: plan caching, typed DAG, supervision, workflow extraction | 13.7, 3.1, 12.3, 15.1 | Attacks 36% of spend at the dearest request |
| 5 | Adaptive: cascades, re-planning, closed-loop concurrency | 9.1, 13.6, 17.1 | Needs the statistics from phase 0 to be safe |
| 6 | Research: reasoning graphs, rate-distortion briefs, learned policies | 11.1, 1.1, 7.1, 7.5 | Only once phases 0 to 2 show the residual cost is reasoning |

### Measurement framework

Every optimisation is held to the same five answers.

1. **Which objective?** TOK, LAT, COR or THR. Nothing claims all four.
2. **Which token kind?** Cache reads, cache writes, or output. A cache-read
   claim is reported at three weightings: raw, API price, and cache reads at
   zero.
3. **What is the denominator?** Tokens for each verified, integrated change.
   Never tokens for each session.
4. **What is the design?** Paired tasks with a size covariate, 18.3, or a
   switchback over time blocks, 18.4, with sequential stopping, 18.5. Where the
   effect is small, an off-policy estimate from the archive, 18.6, before any
   live run.
5. **What would disprove it?** Stated before the run. A test proves a fix only
   if it fails without the fix.

Quality is tracked beside cost: verdict mix, retries, rework, and the FAIL rate
by task size. A saving that moves the FAIL rate is not a saving.

### Novelty-gap analysis

Five combinations look underexplored, each with a testable hypothesis.

1. **Weighted attribution driving model routing.** Nearly all published token
   advice counts raw tokens. *Hypothesis:* routing by output-weighted cost
   changes the chosen model for more than a third of task classes.
2. **Blast-radius admission control.** Conflict avoidance is usually a merge-time
   concern. A reachability check at admission is not standard.
   *Hypothesis:* refusing overlapping assignments cuts rework more than
   semantic merge would, at a fraction of the effort.
3. **Anytime briefs with adaptive re-planning instead of a hard ceiling.** The
   literature offers budgets. It rarely requires a usable partial result.
   *Hypothesis:* a brief demanding a usable partial result at each checkpoint
   makes a budget safe. A bare ceiling cuts into real work.
4. **Plan caching for the coordinator.** Attention goes to worker prompts. The coordinator's planning cost is
   ignored. *Hypothesis:* a cached plan shape removes most of the 6.6M tokens
   the Master spends between task starts.
5. **Proxy measurement of re-derived reasoning.** Nobody counts the cost that
   leaves no tool call. *Hypothesis:* counting conclusions restated across
   tasks bounds a cost now assumed to be zero. Embed the decision lines in
   result files to count them.
