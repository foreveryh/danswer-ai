from onyx.document_index.vespa.shared_utils.utils import remove_invalid_unicode_chars


def test_remove_invalid_unicode_chars() -> None:
    """Test that invalid Unicode characters are properly removed."""
    # Test removal of illegal XML character 0xFDDB
    text_with_illegal_char = "Valid text \uFDDB more text"
    sanitized = remove_invalid_unicode_chars(text_with_illegal_char)
    assert "\uFDDB" not in sanitized
    assert sanitized == "Valid text  more text"

    # Test that valid characters are preserved
    valid_text = "Hello, world! 你好世界"
    assert remove_invalid_unicode_chars(valid_text) == valid_text

    # Test multiple invalid characters including 0xFDDB
    text_with_multiple_illegal = "\x00Hello\uFDDB World\uFFFE!"
    sanitized = remove_invalid_unicode_chars(text_with_multiple_illegal)
    assert all(c not in sanitized for c in ["\x00", "\uFDDB", "\uFFFE"])
    assert sanitized == "Hello World!"
