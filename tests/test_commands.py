"""CMD-block extraction — models routinely fence a command block with a language
tag ("```json") despite being told to use ```CMD or a bare fence; measured live
against minimax-m3, which silently discarded a whole well-formed character
invention because the fence wasn't recognized at all.
"""
from loom.commands import parse_cmd_block, split_cmd_response


def test_json_tagged_fence_is_still_recognized_as_a_bare_cmd_block():
    text = ('Meet Eunice.\n\n```json\nSET cast [{"name":"Eunice"}]\n'
            'SET fields.character_cores {"eunice":"Guards the archive."}\n```\n')
    prose, commands = split_cmd_response(text)
    assert prose == "Meet Eunice."
    assert commands == [
        {"op": "SET", "path": "cast", "value": [{"name": "Eunice"}]},
        {"op": "SET", "path": "fields.character_cores", "value": {"eunice": "Guards the archive."}},
    ]


def test_cmd_tagged_fence_still_works_unchanged():
    text = '```CMD\nSET premise "A woman returns home by ferry."\n```'
    prose, commands = split_cmd_response(text)
    assert prose == ""
    assert commands == [{"op": "SET", "path": "premise", "value": "A woman returns home by ferry."}]


def test_bare_fence_with_no_tag_still_works_unchanged():
    text = '```\nSET premise "A woman returns home by ferry."\n```'
    prose, commands = split_cmd_response(text)
    assert commands == [{"op": "SET", "path": "premise", "value": "A woman returns home by ferry."}]


def test_an_untested_tag_word_is_also_tolerated():
    # The first fix used a 5-word whitelist (json/cmd/text/txt/yaml). Confirmed live
    # that this DIDN'T generalize: "```python" (never observed, but just as plausible
    # a model choice as "```json") fell straight through to no command found at all.
    # The real fix accepts any single tag word, not a fixed list.
    text = '```python\nSET premise "x"\n```'
    _prose, commands = split_cmd_response(text)
    assert commands == [{"op": "SET", "path": "premise", "value": "x"}]


def test_a_same_line_fence_does_not_get_its_command_eaten_as_a_tag():
    # The reason the fix isn't just "any word before a newline, unconditionally": a
    # fence with no tag and no line break — "```SET premise ..." — must still resolve
    # SET as the command, not have "SET" itself misread as the fence's language tag
    # (which would leave nothing to parse). The mandatory-newline requirement on the
    # tag branch is what prevents that; this is the case it exists to protect.
    commands = parse_cmd_block('SET premise "x"')
    assert commands == [{"op": "SET", "path": "premise", "value": "x"}]
    text = '```SET premise "x"```'
    _prose, commands = split_cmd_response(text)
    assert commands == [{"op": "SET", "path": "premise", "value": "x"}]


def test_a_single_backtick_inline_command_is_recognized():
    # Measured live (deepseek-chat): a short command formatted as inline code rather
    # than a fenced block — `SET fields.open_questions [...]` — was previously invisible
    # to every parsing strategy and silently dropped as ordinary prose.
    text = 'Noted.\n`SET fields.open_questions ["What happened to the child?"]`\nMore text after.'
    prose, commands = split_cmd_response(text)
    assert commands == [{"op": "SET", "path": "fields.open_questions", "value": ["What happened to the child?"]}]
    assert "`" not in prose
    assert "Noted." in prose and "More text after." in prose


def test_an_inline_command_does_not_swallow_unrelated_backtick_prose():
    # [^`\n] keeps the match to one line and one backtick pair — ordinary inline code
    # elsewhere in the reply (e.g. a field name mentioned in prose) must survive intact.
    text = 'The `premise` field is already set.\n`SET fields.open_questions ["x"]`'
    prose, commands = split_cmd_response(text)
    assert commands == [{"op": "SET", "path": "fields.open_questions", "value": ["x"]}]
    assert "`premise`" in prose


if __name__ == "__main__":
    test_json_tagged_fence_is_still_recognized_as_a_bare_cmd_block()
    test_cmd_tagged_fence_still_works_unchanged()
    test_bare_fence_with_no_tag_still_works_unchanged()
    test_an_untested_tag_word_is_also_tolerated()
    test_a_same_line_fence_does_not_get_its_command_eaten_as_a_tag()
    test_a_single_backtick_inline_command_is_recognized()
    test_an_inline_command_does_not_swallow_unrelated_backtick_prose()
    print("ok — CMD fence-tag tolerance")
