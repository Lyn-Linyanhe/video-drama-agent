# Script Input Modes Design

## Goal

Separate short story ideas from existing screenplay input while preserving the
user's original text. Generated or understood structure must remain editable
both directly and through natural-language revision requests.

## Modes

- `idea`: the user supplies a short premise and explicitly runs **AI Generate
  Script**. The resulting script is reviewed and edited before the user runs
  **AI Analyze Script**.
- `script`: the user pastes or imports a complete screenplay and explicitly
  runs **AI Analyze Script**. This path has no script-generation step. Analysis
  must understand the supplied manuscript faithfully without inventing new
  plot, changing the ending, or replacing the original manuscript.

## Input and Persistence

The original user input remains in `DramaState.user_input`. `input_mode` and
`source_filename` are persisted with the project. For idea mode, analysis is
performed on the reviewed generated scene script while `user_input` still keeps
the original premise. TXT, MD, and DOCX imports extract text into the manuscript
editor; no file content is overwritten by LLM output. DOCX is parsed using the
Python standard library ZIP/XML support.

## Results and Editing

The default result below the source editor is a human-readable analysis: title
and logline, story overview, characters and dialogue count, turning points,
scene timeline, and a production scene table. The raw `ScriptBreakdown` JSON is
available in a collapsed advanced-edit panel. Users can save manual structured
edits after schema validation or submit a natural-language instruction to revise
the result. Both actions refresh the readable view, persist project state, and
preserve the source text.
