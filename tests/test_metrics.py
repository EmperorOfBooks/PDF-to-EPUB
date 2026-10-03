from __future__ import annotations

from metrics import detect_junk_paragraphs, paragraph_stats, word_recall


def test_paragraph_stats_detect_fragmentation():
    paragraphs = [
        'This is a valid paragraph with a trailing sentence.',
        'this is a lowercase-start paragraph fragment.',
        'Another normal paragraph.',
        '1',
        '&',
    ]
    stats = paragraph_stats(paragraphs)

    assert stats['paragraph_count'] == 5
    assert stats['lowercase_start_ratio'] > 0.0
    assert 'under_40_chars' in stats
    assert detect_junk_paragraphs(['1', '&']) == ['1', '&']


def test_word_recall_tracks_vocab_drift():
    reference = ['The quick brown fox jumps over the lazy dog.']
    extracted = ['The quick brown fox jumps over the lazy dog.']
    result = word_recall(reference, extracted)

    assert result['recall'] == 1.0
    assert result['missing_word_count'] == 0
    assert result['spurious_word_count'] == 0
