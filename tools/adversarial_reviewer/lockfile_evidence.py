"""Exact npm lock records with shared structural metadata, never executed."""
import json
from pathlib import PurePosixPath


def object_members(source, start):
    decoder = json.JSONDecoder()
    cursor = start + 1
    while True:
        while source[cursor].isspace():
            cursor += 1
        if source[cursor] == '}':
            return
        key_start = cursor
        key, cursor = decoder.raw_decode(source, cursor)
        while source[cursor].isspace():
            cursor += 1
        if source[cursor] != ':':
            raise ValueError('invalid json member')
        cursor += 1
        while source[cursor].isspace():
            cursor += 1
        value_start = cursor
        _, cursor = decoder.raw_decode(source, cursor)
        yield key, key_start, value_start, cursor
        while source[cursor].isspace():
            cursor += 1
        if source[cursor] == ',':
            cursor += 1
        elif source[cursor] != '}':
            raise ValueError('invalid json separator')


def npm_records(path, source):
    if PurePosixPath(path).name != 'package-lock.json':
        return None

    def unique_members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate json member')
            result[key] = value
        return result

    try:
        parsed = json.loads(source, object_pairs_hook=unique_members)
        if (not isinstance(parsed, dict) or parsed.get('lockfileVersion') != 3
                or not isinstance(parsed.get('packages'), dict)):
            return None
        if any(not isinstance(entry, dict) or entry.get('link')
               or str(entry.get('resolved', '')).startswith(('file:', '../', './'))
               for entry in parsed['packages'].values()):
            return None
        records = []
        start = len(source) - len(source.lstrip())
        records.append((0, start + 1, 'metadata'))
        for key, begin, value, end in object_members(source, start):
            if key != 'packages':
                records.append((begin, end, 'metadata'))
                continue
            records.append((begin, value + 1, 'metadata'))
            for package, entry_start, _, entry_end in object_members(source, value):
                records.append((entry_start, entry_end, 'package:' + package if package else 'metadata'))
            records.append((end - 1, end, 'metadata'))
        records.append((source.rfind('}'), len(source), 'metadata'))
        return records
    except (ValueError, IndexError, TypeError):
        return None
