from pathlib import Path


def test_imported_script_bypasses_draft_editor_and_can_be_confirmed_directly():
    source = Path("web/js/pages/script.js").read_text(encoding="utf-8")

    start = source.index("async function saveImportedDraft()")
    end = source.index("async function saveDraft()", start)
    handler = source[start:end]

    assert "openDraftModal()" not in handler
    assert "persistScriptText(text, 'script')" in handler
    assert "confirmDraftAndAnalyze({ skipSave: true })" in handler
    assert "catch (error)" in handler


def test_new_project_modal_does_not_offer_script_source_selection():
    source = Path("studio.html").read_text(encoding="utf-8")

    assert 'id="project-script-source"' not in source
    assert 'id="script-source-choice"' not in source
    assert 'onclick="StudioActions.saveImportedDraft()">确认并解析</button>' in source


def test_script_editor_stops_follow_up_actions_when_draft_save_fails():
    source = Path("web/js/pages/script.js").read_text(encoding="utf-8")
    start = source.index("async function reviseDraft()")
    end = source.index("async function confirmDraftAndAnalyze(", start)
    revise = source[start:end]

    assert "if (!(await saveDraft())) return;" in revise
    assert "catch (error)" in revise


def test_script_page_binds_the_shared_project_request_helper():
    source = Path("web/js/pages/script.js").read_text(encoding="utf-8")
    first_dependency_block = source[:source.index("let activeAssetTab")]

    assert "const projectJsonRequest = (...args) => globalThis.StudioActions.projectJsonRequest(...args);" in first_dependency_block
