from form_schema import (
    sanitize_fields, sanitize_settings, prepare_schema, validate_submission,
    summarize, column_headers, format_value,
)


def make_schema(fields, settings=None):
    return prepare_schema({'fields': fields, 'settings': settings or {}})


def test_sanitize_fields_drops_unknown_types_and_assigns_unique_keys():
    fields = sanitize_fields([
        {'id': 'a', 'type': 'bogus', 'label': 'Name'},
        {'id': 'b', 'type': 'text', 'label': 'Name'},
        {'id': 'c', 'type': 'section', 'label': 'Page 2'},
        {'id': 'd', 'type': 'email', 'label': ''},
    ])
    assert [f['type'] for f in fields] == ['text', 'text', 'section', 'email']
    assert [f.get('key') for f in fields] == ['Name', 'Name (2)', None, 'Question 3']


def test_sanitize_fields_rejects_bad_ids_and_forward_logic():
    fields = sanitize_fields([
        {'id': '<script>', 'type': 'text', 'label': 'A', 'logic': {'field': 'q2', 'op': 'answered'}},
        {'id': 'q2', 'type': 'yesno', 'label': 'B'},
        {'id': 'q3', 'type': 'text', 'label': 'C', 'logic': {'field': 'q2', 'op': 'equals', 'value': 'Yes'}},
    ])
    assert fields[0]['id'] == 'field_1'
    assert 'logic' not in fields[0]
    assert fields[2]['logic'] == {'field': 'q2', 'op': 'equals', 'value': 'Yes'}


def test_sanitize_settings_defaults_and_bounds():
    settings = sanitize_settings({'theme_color': 'neon', 'response_limit': '0', 'close_at': 'nope'})
    assert settings['theme_color'] == 'indigo'
    assert settings['response_limit'] == 1
    assert settings['close_at'] == ''
    assert settings['show_progress'] is True


def test_validate_submission_required_and_formats():
    schema = make_schema([
        {'id': 'n', 'type': 'text', 'label': 'Name', 'required': True, 'min_length': 2},
        {'id': 'e', 'type': 'email', 'label': 'Email'},
        {'id': 'num', 'type': 'number', 'label': 'Age', 'min': 18, 'max': 99},
        {'id': 'c', 'type': 'checkbox', 'label': 'Pick', 'options': ['A', 'B', 'C'], 'max_select': 2},
    ])
    data, errors = validate_submission(schema, {'n': 'X', 'e': 'not-an-email', 'num': '12', 'c': ['A', 'B', 'C']})
    assert set(errors) == {'n', 'e', 'num', 'c'}

    data, errors = validate_submission(schema, {'n': 'Ada', 'e': 'ada@example.org', 'num': '42', 'c': ['A']})
    assert errors == {}
    assert data == {'Name': 'Ada', 'Email': 'ada@example.org', 'Age': 42, 'Pick': ['A']}


def test_validate_submission_rejects_unknown_choice_unless_other_allowed():
    schema = make_schema([
        {'id': 'r', 'type': 'radio', 'label': 'Colour', 'options': ['Red', 'Blue']},
        {'id': 'o', 'type': 'radio', 'label': 'Fruit', 'options': ['Apple'], 'allow_other': True},
    ])
    _, errors = validate_submission(schema, {'r': 'Green', 'o': 'Mango'})
    assert errors == {'r': 'Please choose a valid option.'}


def test_hidden_questions_are_not_required_and_are_dropped():
    schema = make_schema([
        {'id': 'q1', 'type': 'yesno', 'label': 'Attending?', 'required': True},
        {'id': 'q2', 'type': 'text', 'label': 'Diet', 'required': True,
         'logic': {'field': 'q1', 'op': 'equals', 'value': 'Yes'}},
        {'id': 's', 'type': 'section', 'label': 'Travel', 'logic': {'field': 'q1', 'op': 'equals', 'value': 'Yes'}},
        {'id': 'q3', 'type': 'text', 'label': 'City', 'required': True},
    ])
    data, errors = validate_submission(schema, {'q1': 'No', 'q2': 'vegan'})
    assert errors == {}
    assert data == {'Attending?': 'No'}

    _, errors = validate_submission(schema, {'q1': 'Yes'})
    assert set(errors) == {'q2', 'q3'}


def test_scale_matrix_and_ranking_validation():
    schema = make_schema([
        {'id': 's', 'type': 'scale', 'label': 'Scale', 'min': 1, 'max': 5},
        {'id': 'm', 'type': 'matrix', 'label': 'Grid', 'rows': ['R1', 'R2'], 'columns': ['Bad', 'Good'], 'required': True},
        {'id': 'k', 'type': 'ranking', 'label': 'Rank', 'options': ['X', 'Y']},
    ])
    _, errors = validate_submission(schema, {'s': 9, 'm': {'R1': 'Good'}, 'k': ['X']})
    assert set(errors) == {'s', 'm', 'k'}
    data, errors = validate_submission(schema, {'s': 4, 'm': {'R1': 'Good', 'R2': 'Bad'}, 'k': ['Y', 'X']})
    assert errors == {}
    assert data['Grid'] == {'R1': 'Good', 'R2': 'Bad'}
    assert format_value(data['Grid']) == 'R1: Good; R2: Bad'


def test_summarize_counts_and_nps():
    schema = make_schema([
        {'id': 'c', 'type': 'radio', 'label': 'Colour', 'options': ['Red', 'Blue']},
        {'id': 'n', 'type': 'nps', 'label': 'NPS'},
        {'id': 't', 'type': 'text', 'label': 'Comment'},
    ])
    datas = [
        {'Colour': 'Red', 'NPS': 10, 'Comment': 'great'},
        {'Colour': 'Red', 'NPS': 9},
        {'Colour': 'Blue', 'NPS': 3},
    ]
    colour, nps, comment = summarize(schema, datas)
    assert [(b['label'], b['count']) for b in colour['bars']] == [('Red', 2), ('Blue', 1)]
    assert nps['nps'] == 33
    assert nps['average'] == 7.33
    assert comment['samples'] == ['great']


def test_column_headers_follow_form_order_then_legacy_keys():
    schema = make_schema([
        {'id': 'b', 'type': 'text', 'label': 'Second'},
        {'id': 'a', 'type': 'text', 'label': 'First'},
    ])
    assert column_headers(schema, [{'First': 1, 'Old': 2}]) == ['Second', 'First', 'Old']
