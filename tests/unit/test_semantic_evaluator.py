"""Accounting checks for the independent contextual challenge evaluator."""
from scripts.evaluate_semantic import percentile, summary


def test_summary_keeps_document_offsets_separate_and_requires_category():
    # Both examples use identical offsets, but only the first is correctly
    # labelled. A merged position set would conceal the second document's miss.
    rows = [
        {
            'text': 'ab--',
            'gold': [{'category': 'FULL_NAME', 'start': 0, 'end': 2}],
            'predicted': [{'category': 'FULL_NAME', 'start': 0, 'end': 2}],
            'missing': [], 'unexpected': [], 'exact': True,
            'roundtrip_ok': True, 'mask_ms': [1., 3.], 'unmask_ms': [.1, .3],
        },
        {
            'text': 'cd--',
            'gold': [{'category': 'FULL_NAME', 'start': 0, 'end': 2}],
            'predicted': [{'category': 'PHONE', 'start': 2, 'end': 4}],
            'missing': [{}], 'unexpected': [{}], 'exact': False,
            'roundtrip_ok': True, 'mask_ms': [2., 4.], 'unmask_ms': [.2, .4],
        },
    ]
    result = summary(rows)
    assert result['exact_cases'] == 1
    assert result['cases'] == result['roundtrip_cases'] == 2
    assert result['document_mean_concealment'] == .5
    assert result['document_mean_overmask_rate'] == .5
    assert result['per_category']['FULL_NAME']['recall'] == .5
    assert result['per_category']['PHONE']['precision'] == 0
    assert result['missing_exact_spans'] == result['unexpected_exact_spans'] == 1
    assert result['mask_timing']['observations'] == 4
    assert result['mask_timing']['p50_ms'] == 2.5


def test_percentiles_use_interpolation_and_handle_small_samples():
    assert percentile([], .99) is None
    assert percentile([7.], .99) == 7.
    assert percentile([0., 100.], .95) == 95.


def test_character_metric_accepts_segmentation_but_checks_values_and_category():
    from scripts.evaluate_semantic import character_outcome

    text = '45 11 654321'
    gold = [{'category': 'PASSPORT', 'start': 0, 'end': len(text)}]
    split = [
        {'category': 'PASSPORT', 'start': 0, 'end': 5},
        {'category': 'PASSPORT', 'start': 6, 'end': len(text)},
    ]
    result = character_outcome(text, gold, split)
    assert result['sensitive_character_exact']
    assert result['category_character_exact']
    split[1]['end'] -= 1
    assert character_outcome(text, gold, split)['missing_sensitive_characters'] == 1
    split[1]['end'] += 1
    split[1]['category'] = 'PHONE'
    result = character_outcome(text, gold, split)
    assert result['sensitive_character_exact']
    assert not result['category_character_exact']
