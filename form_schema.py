"""Form schema handling: sanitising builder output, validating submissions
and summarising responses.

A form schema is a JSON document stored in ``Form.schema``::

    {
        "description_html": "...", "description": "...",
        "header_image_url": "...",
        "settings": {...},          # see sanitize_settings()
        "fields": [{...}, ...]      # see sanitize_field()
    }

Submissions are stored keyed by each field's ``key`` (a unique version of its
label) so existing exports keep working for older forms.
"""
import re
from datetime import datetime

INPUT_TYPES = {
    'text', 'textarea', 'email', 'number', 'phone', 'url', 'date', 'time',
    'radio', 'checkbox', 'dropdown', 'yesno',
    'scale', 'rating', 'nps', 'matrix', 'ranking',
}
LAYOUT_TYPES = {'section', 'statement'}
FIELD_TYPES = INPUT_TYPES | LAYOUT_TYPES
CHOICE_TYPES = {'radio', 'checkbox', 'dropdown'}
OPTION_TYPES = CHOICE_TYPES | {'ranking'}
LOGIC_OPS = {'equals', 'not_equals', 'contains', 'answered', 'not_answered', 'gt', 'lt'}
THEME_COLORS = {'indigo', 'violet', 'blue', 'teal', 'green', 'amber', 'rose', 'slate'}

FIELD_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_PATTERN = re.compile(r'^[0-9+()\-.\s]{5,32}$')
URL_PATTERN = re.compile(r'^https?://\S+$', re.IGNORECASE)
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TIME_PATTERN = re.compile(r'^\d{2}:\d{2}$')

MAX_FIELDS = 200
MAX_OPTIONS = 100
MAX_LABEL = 500
MAX_TEXT = 2000
MAX_ANSWER = 10000


def _str(value, limit=MAX_LABEL):
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def _bool(value):
    return value is True or value in ('true', '1', 1, 'on')


def _int(value, default=None, lo=None, hi=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None and number < lo:
        number = lo
    if hi is not None and number > hi:
        number = hi
    return number


def _num(value):
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_list(values, limit=MAX_OPTIONS):
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values[:limit]:
        text = _str(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def sanitize_logic(raw):
    if not isinstance(raw, dict):
        return None
    field = _str(raw.get('field'), 64)
    op = _str(raw.get('op'), 32)
    if not FIELD_ID_PATTERN.match(field) or op not in LOGIC_OPS:
        return None
    return {'field': field, 'op': op, 'value': _str(raw.get('value'))}


def sanitize_field(raw, index):
    if not isinstance(raw, dict):
        return None
    field_type = raw.get('type') if raw.get('type') in FIELD_TYPES else 'text'
    field_id = _str(raw.get('id'), 64)
    if not FIELD_ID_PATTERN.match(field_id):
        field_id = f'field_{index + 1}'

    field = {
        'id': field_id,
        'type': field_type,
        'label': _str(raw.get('label')),
        'description': _str(raw.get('description'), MAX_TEXT),
    }
    if field_type in INPUT_TYPES:
        field['required'] = _bool(raw.get('required'))
        placeholder = _str(raw.get('placeholder'), 200)
        if placeholder:
            field['placeholder'] = placeholder

    if field_type in OPTION_TYPES:
        field['options'] = _str_list(raw.get('options')) or ['Option 1']
        field['shuffle'] = _bool(raw.get('shuffle'))
    if field_type in {'radio', 'checkbox'}:
        field['allow_other'] = _bool(raw.get('allow_other'))
    if field_type == 'checkbox':
        field['min_select'] = _int(raw.get('min_select'), None, 0, MAX_OPTIONS)
        field['max_select'] = _int(raw.get('max_select'), None, 1, MAX_OPTIONS)

    if field_type in {'text', 'textarea'}:
        field['min_length'] = _int(raw.get('min_length'), None, 0, MAX_ANSWER)
        field['max_length'] = _int(raw.get('max_length'), None, 1, MAX_ANSWER)
    if field_type == 'number':
        field['min'] = _num(raw.get('min'))
        field['max'] = _num(raw.get('max'))

    if field_type == 'scale':
        field['min'] = _int(raw.get('min'), 1, 0, 1)
        field['max'] = _int(raw.get('max'), 5, 2, 10)
        field['min_label'] = _str(raw.get('min_label'), 100)
        field['max_label'] = _str(raw.get('max_label'), 100)
    if field_type == 'rating':
        field['max'] = _int(raw.get('max'), 5, 3, 10)
    if field_type == 'nps':
        field['min_label'] = _str(raw.get('min_label'), 100) or 'Not at all likely'
        field['max_label'] = _str(raw.get('max_label'), 100) or 'Extremely likely'
    if field_type == 'matrix':
        field['rows'] = _str_list(raw.get('rows')) or ['Row 1']
        field['columns'] = _str_list(raw.get('columns'), 20) or ['Column 1']

    logic = sanitize_logic(raw.get('logic'))
    if logic and logic['field'] != field_id:
        field['logic'] = logic
    return field


def assign_keys(fields):
    """Give every input field a unique, label-based ``key`` used for storage."""
    seen = set()
    number = 0
    for field in fields:
        if field.get('type') in LAYOUT_TYPES:
            field.pop('key', None)
            continue
        number += 1
        base = (field.get('label') or '').strip() or f'Question {number}'
        key = base
        suffix = 2
        while key.casefold() in seen:
            key = f'{base} ({suffix})'
            suffix += 1
        seen.add(key.casefold())
        field['key'] = key
    return fields


def sanitize_settings(raw):
    if not isinstance(raw, dict):
        raw = {}
    close_at = _str(raw.get('close_at'), 32)
    if close_at:
        try:
            close_at = datetime.fromisoformat(close_at).strftime('%Y-%m-%dT%H:%M')
        except ValueError:
            close_at = ''
    theme = raw.get('theme_color')
    return {
        'theme_color': theme if theme in THEME_COLORS else 'indigo',
        'confirmation_message': _str(raw.get('confirmation_message'), MAX_TEXT),
        'one_response_per_user': _bool(raw.get('one_response_per_user')),
        'response_limit': _int(raw.get('response_limit'), None, 1, 1_000_000),
        'close_at': close_at,
        'show_progress': raw.get('show_progress', True) is not False,
        'show_question_numbers': _bool(raw.get('show_question_numbers')),
        'collect_email': _bool(raw.get('collect_email')),
    }


def sanitize_fields(raw_fields):
    if not isinstance(raw_fields, list):
        return []
    fields = []
    used_ids = set()
    for index, raw in enumerate(raw_fields[:MAX_FIELDS]):
        field = sanitize_field(raw, index)
        if not field:
            continue
        if field['id'] in used_ids:
            field['id'] = f"{field['id']}_{index + 1}"
        used_ids.add(field['id'])
        fields.append(field)
    # Drop logic that points at a missing or later question.
    earlier = set()
    for field in fields:
        logic = field.get('logic')
        if logic and logic['field'] not in earlier:
            field.pop('logic')
        if field['type'] in INPUT_TYPES:
            earlier.add(field['id'])
    return assign_keys(fields)


def prepare_schema(schema):
    """Normalise a stored schema (possibly from an older version) for rendering."""
    if not isinstance(schema, dict):
        schema = {}
    schema = dict(schema)
    schema['fields'] = sanitize_fields(schema.get('fields'))
    schema['settings'] = sanitize_settings(schema.get('settings'))
    return schema


def input_fields(schema):
    return [f for f in schema.get('fields', []) if f.get('type') in INPUT_TYPES]


# ---------------------------------------------------------------------------
# Submission validation
# ---------------------------------------------------------------------------

def is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ''
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _as_text_list(value):
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return [str(v) for v in value.values()]
    if value is None:
        return []
    return [str(value)]


def logic_matches(logic, answers_by_id):
    """Evaluate a single display condition against raw answers keyed by field id."""
    if not logic:
        return True
    value = answers_by_id.get(logic['field'])
    op = logic['op']
    target = (logic.get('value') or '').strip().casefold()
    if op == 'answered':
        return not is_empty(value)
    if op == 'not_answered':
        return is_empty(value)
    values = [v.strip().casefold() for v in _as_text_list(value)]
    if op == 'equals':
        return target in values
    if op == 'not_equals':
        return target not in values
    if op == 'contains':
        return any(target in v for v in values)
    if op in {'gt', 'lt'}:
        left, right = _num(values[0] if values else None), _num(target)
        if left is None or right is None:
            return False
        return left > right if op == 'gt' else left < right
    return True


def visible_field_ids(schema, answers_by_id):
    """Return ids of fields visible for these answers (sections hide their contents)."""
    visible = set()
    section_visible = True
    for field in schema.get('fields', []):
        if field['type'] == 'section':
            section_visible = logic_matches(field.get('logic'), answers_by_id)
            if section_visible:
                visible.add(field['id'])
            continue
        if section_visible and logic_matches(field.get('logic'), answers_by_id):
            visible.add(field['id'])
    return visible


def _clean_answer(field, value):
    """Return (clean_value, error). ``value`` has already been checked non-empty."""
    ftype = field['type']
    if ftype in {'text', 'textarea', 'email', 'phone', 'url', 'date', 'time'}:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None, 'Invalid answer.'
        text = str(value).strip()[:MAX_ANSWER]
        if ftype in {'text', 'textarea'}:
            if field.get('min_length') and len(text) < field['min_length']:
                return None, f"Must be at least {field['min_length']} characters."
            if field.get('max_length') and len(text) > field['max_length']:
                return None, f"Must be at most {field['max_length']} characters."
        checks = {'email': EMAIL_PATTERN, 'phone': PHONE_PATTERN, 'url': URL_PATTERN,
                  'date': DATE_PATTERN, 'time': TIME_PATTERN}
        pattern = checks.get(ftype)
        if pattern and not pattern.match(text):
            return None, 'Invalid format.'
        return text, None

    if ftype == 'number':
        number = _num(value)
        if number is None:
            return None, 'Must be a number.'
        if field.get('min') is not None and number < field['min']:
            return None, f"Must be at least {field['min']:g}."
        if field.get('max') is not None and number > field['max']:
            return None, f"Must be at most {field['max']:g}."
        return int(number) if number.is_integer() else number, None

    if ftype in {'radio', 'dropdown', 'yesno'}:
        allowed = ['Yes', 'No'] if ftype == 'yesno' else field.get('options', [])
        text = _str(value, MAX_ANSWER)
        if text in allowed or (field.get('allow_other') and text):
            return text, None
        return None, 'Please choose a valid option.'

    if ftype == 'checkbox':
        if not isinstance(value, list):
            value = [value]
        chosen = []
        for item in value:
            text = _str(item, MAX_ANSWER)
            if not text or text in chosen:
                continue
            if text not in field.get('options', []) and not field.get('allow_other'):
                return None, 'Please choose valid options.'
            chosen.append(text)
        if field.get('min_select') and len(chosen) < field['min_select']:
            return None, f"Select at least {field['min_select']}."
        if field.get('max_select') and len(chosen) > field['max_select']:
            return None, f"Select at most {field['max_select']}."
        return chosen, None

    if ftype in {'scale', 'rating', 'nps'}:
        number = _int(value)
        lo = {'scale': field.get('min', 1), 'rating': 1, 'nps': 0}[ftype]
        hi = {'scale': field.get('max', 5), 'rating': field.get('max', 5), 'nps': 10}[ftype]
        if number is None or number < lo or number > hi:
            return None, 'Please choose a value.'
        return number, None

    if ftype == 'matrix':
        if not isinstance(value, dict):
            return None, 'Invalid answer.'
        rows, columns = field.get('rows', []), field.get('columns', [])
        clean = {}
        for row in rows:
            choice = value.get(row)
            if is_empty(choice):
                continue
            if choice not in columns:
                return None, 'Please choose valid options.'
            clean[row] = choice
        if field.get('required') and len(clean) < len(rows):
            return None, 'Please answer every row.'
        return clean, None

    if ftype == 'ranking':
        options = field.get('options', [])
        if not isinstance(value, list) or sorted(map(str, value)) != sorted(options):
            return None, 'Please rank all options.'
        return [str(v) for v in value], None

    return None, 'Unsupported question.'


def validate_submission(schema, payload):
    """Validate a submission payload.

    ``payload`` maps field id -> raw answer (the form sends ``{"answers": {...}}``).
    Returns ``(data, errors)`` where ``data`` is keyed by field key for storage
    and ``errors`` maps field id -> message.
    """
    answers_by_id = payload if isinstance(payload, dict) else {}
    visible = visible_field_ids(schema, answers_by_id)
    data, errors = {}, {}
    for field in input_fields(schema):
        if field['id'] not in visible:
            continue
        raw = answers_by_id.get(field['id'])
        if is_empty(raw):
            if field.get('required'):
                errors[field['id']] = 'This question is required.'
            continue
        clean, error = _clean_answer(field, raw)
        if error:
            errors[field['id']] = error
        elif not is_empty(clean):
            data[field['key']] = clean
    return data, errors


# ---------------------------------------------------------------------------
# Presentation helpers for exports and analytics
# ---------------------------------------------------------------------------

def format_value(value):
    if isinstance(value, list):
        return ', '.join(map(str, value))
    if isinstance(value, dict):
        return '; '.join(f'{k}: {v}' for k, v in value.items())
    if value is None:
        return ''
    return value


def column_headers(schema, submissions_data):
    """Question columns in form order, followed by any legacy keys."""
    headers = [f['key'] for f in input_fields(schema)]
    known = {h.casefold() for h in headers}
    for data in submissions_data:
        for key in data:
            if key.casefold() not in known:
                headers.append(key)
                known.add(key.casefold())
    return headers


def summarize(schema, submissions_data):
    """Aggregate answers per question for the analytics view."""
    total = len(submissions_data)
    summary = []
    for field in input_fields(schema):
        key, ftype = field['key'], field['type']
        values = [d.get(key) for d in submissions_data if not is_empty(d.get(key))]
        item = {'field': field, 'answered': len(values), 'total': total}

        if ftype in CHOICE_TYPES or ftype == 'yesno':
            options = ['Yes', 'No'] if ftype == 'yesno' else list(field.get('options', []))
            counts = {opt: 0 for opt in options}
            other = 0
            for value in values:
                for choice in (value if isinstance(value, list) else [value]):
                    if choice in counts:
                        counts[choice] += 1
                    else:
                        other += 1
            if other:
                counts['Other'] = counts.get('Other', 0) + other
            item['kind'] = 'bars'
            item['bars'] = _bars(counts, len(values))
        elif ftype in {'scale', 'rating', 'nps', 'number'}:
            numbers = [n for n in (_num(v) for v in values) if n is not None]
            item['kind'] = 'numeric'
            item['average'] = round(sum(numbers) / len(numbers), 2) if numbers else None
            item['min'] = min(numbers) if numbers else None
            item['max'] = max(numbers) if numbers else None
            if ftype != 'number':
                lo = 0 if ftype == 'nps' else field.get('min', 1) if ftype == 'scale' else 1
                hi = 10 if ftype == 'nps' else field.get('max', 5)
                counts = {str(i): 0 for i in range(lo, hi + 1)}
                for n in numbers:
                    if str(int(n)) in counts:
                        counts[str(int(n))] += 1
                item['bars'] = _bars(counts, len(numbers))
            if ftype == 'nps' and numbers:
                promoters = sum(1 for n in numbers if n >= 9)
                detractors = sum(1 for n in numbers if n <= 6)
                item['nps'] = round((promoters - detractors) * 100 / len(numbers))
        elif ftype == 'matrix':
            item['kind'] = 'matrix'
            columns = field.get('columns', [])
            item['columns'] = columns
            item['rows'] = []
            for row in field.get('rows', []):
                counts = [sum(1 for v in values if isinstance(v, dict) and v.get(row) == c)
                          for c in columns]
                item['rows'].append({'label': row, 'counts': counts})
        elif ftype == 'ranking':
            options = field.get('options', [])
            scores = {opt: [] for opt in options}
            for value in values:
                if isinstance(value, list):
                    for position, opt in enumerate(value, start=1):
                        if opt in scores:
                            scores[opt].append(position)
            ranked = sorted(
                ((opt, sum(p) / len(p)) for opt, p in scores.items() if p),
                key=lambda pair: pair[1]
            )
            item['kind'] = 'ranking'
            item['ranking'] = [{'label': o, 'average': round(a, 2)} for o, a in ranked]
        else:
            item['kind'] = 'text'
            item['samples'] = [str(v) for v in values[-5:]][::-1]
        summary.append(item)
    return summary


def _bars(counts, denominator):
    return [
        {'label': label, 'count': count,
         'percent': round(count * 100 / denominator) if denominator else 0}
        for label, count in counts.items()
    ]
