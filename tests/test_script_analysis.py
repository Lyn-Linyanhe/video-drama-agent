from tests.test_prop_assets import (
    test_prune_nonessential_props_removes_one_shot_household_items as _removes_nonessential_props,
    test_reconcile_prop_bindings_uses_shot_bindings_as_source_of_truth as _rebuilds_bindings,
    test_valid_many_props_are_not_truncated_and_emit_density_warning as _keeps_many_valid_props,
)


def test_single_shot_and_household_props_are_removed_with_reason():
    _removes_nonessential_props()


def test_inconsistent_llm_shot_ids_are_rebuilt_from_bindings():
    _rebuilds_bindings()


def test_valid_many_props_are_not_truncated():
    _keeps_many_valid_props()
