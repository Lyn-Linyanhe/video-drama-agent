from pathlib import Path


def test_unstarted_workspace_keeps_both_script_source_entries_visible():
    source = Path("web/js/pages/script.js").read_text(encoding="utf-8")
    start = source.index("function renderScriptWorkspace()")
    end = source.index("async function generateDraftFromIdea()", start)
    workspace = source[start:end]

    assert workspace.count('StudioActions.openIdeaModal()') >= 1
    assert workspace.count('StudioActions.openImportModal()') >= 1
