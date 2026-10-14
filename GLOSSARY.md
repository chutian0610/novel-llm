# Novel LLM

Novel LLM is a single-author workbench for planning, drafting, reviewing, and exporting long-form fiction.

## Language

## Writing

**Author**:
The sole human owner of all Projects and the only source of human Review.
_Avoid_: Account, user

**Project**:
A single fictional work and the container for its chapters, Story Bible, memory, workflow runs, and exports.
_Avoid_: Book, novel when the work may be a different form

**Volume**:
An ordered grouping of chapters inside a Project.
_Avoid_: Arc, season

**Outline**:
The versioned plan containing the work synopsis, volume plans, chapter briefs, scene goals, foreshadowing instructions, and constraints.
_Avoid_: Plot, chapter requirement

**Chapter**:
The primary editorial unit of a Project; it points to the current Canonical Chapter Version.
_Avoid_: Scene, section

**Canonical Chapter Version**:
The immutable chapter text approved for the Project after review.
_Avoid_: Final draft, saved chapter, latest content

**Candidate**:
A model-generated, model-revised, or author-edited chapter variant that has not been approved as canonical.
_Avoid_: Draft when it has not entered review, version when it is not canonical

**Review**:
An Author's decision to approve, reject, or request revision for a Candidate or Bible Change Proposal.
_Avoid_: Evaluation when describing automated model assessment

**Review Item**:
The queue entry that identifies the artifact or proposal awaiting a human Review.
_Avoid_: Pending review, approval task

## Story Bible

**Story Bible**:
The authoritative set of characters, relationships, world rules, events, style constraints, and open questions for a Project.
_Avoid_: Setting file, lore dump

**Bible Draft**:
The editable working state of a Story Bible that has not been published for generation.
_Avoid_: Live bible, current bible

**Published Bible Version**:
An immutable Story Bible snapshot that generation work must reference.
_Avoid_: Approved draft, saved bible

**Bible Change Proposal**:
A reviewed unit of proposed additions, edits, or conflicts for a Bible Draft.
_Avoid_: Patch, suggestion

**Bible Entry**:
A typed fact, relationship, event, style constraint, or open question contained in a Bible Draft or Published Bible Version.
_Avoid_: Lore row, setting item

## Memory

**Bible Memory**:
Long-lived facts and constraints drawn from a Published Bible Version.
_Avoid_: Static prompt, lore

**State Increment Memory**:
The current narrative state derived only from Canonical Chapter Versions.
_Avoid_: Recap, latest context

**Retrieval Memory**:
Searchable source material selected to support a specific generation task.
_Avoid_: Vector memory, RAG context

**Context Package**:
The immutable input bundle assembled for a Job from its bound Bible, canonical chapters, memory, outline, instructions, and retrieved material.
_Avoid_: Prompt when referring to the persisted domain object

**Context Profile**:
The versioned budget, weighting, and selection rules used to assemble a Context Package.
_Avoid_: Retrieval config, prompt settings

**Narrative Time**:
The chronological time at which an event occurs inside the story world.
_Avoid_: Chapter order, timeline when the order is ambiguous

**Disclosure Order**:
The order in which story events are revealed to the reader, which may differ from Narrative Time.
_Avoid_: Chapter order, chronology

**Stale Projection**:
A derived memory state whose source Canonical Chapter Version no longer matches the state used to project it.
_Avoid_: Broken memory, invalid chapter

## Workflow

**Workflow Run**:
One execution of a defined editorial or generation process.
_Avoid_: Job when referring to the whole process

**Job**:
A persistently queued unit of work that a Worker claims and executes.
_Avoid_: Task, process

**Job Type**:
The named kind of work a Job executes, such as Bible interview, chapter generation, memory projection, or export.
_Avoid_: Workflow name, queue name

**Model Profile**:
The versioned model, sampling, timeout, retry, and cost settings bound to a Job.
_Avoid_: Provider key, model name

**Transient Failure**:
A failure caused by a potentially temporary condition, such as a model timeout or rate limit, that may be retried safely.
_Avoid_: Error, bug

**Step**:
A named, checkpointed stage inside a Workflow Run or Job.
_Avoid_: Node, state when referring to persisted progress

**Pause Requested**:
The control state meaning a running Job should stop at the next safe checkpoint.
_Avoid_: Paused before the Job has actually stopped

**Cancel Requested**:
The control state meaning a running Job should stop and not continue its normal outcome.
_Avoid_: Cancelled before the Job has actually stopped

**Artifact**:
An immutable persisted output such as a Candidate, evaluation, diagnosis, revision, export, or context package.
_Avoid_: File when the artifact also has domain identity

**Export**:
A rendered delivery artifact derived from one or more Canonical Chapter Versions.
_Avoid_: Save, download when referring to the domain object
