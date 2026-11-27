"""Read embedded YOLO metadata without loading a second inference runtime."""
import ast


def _varint(data, start):
    value = 0
    for shift in range(0, 64, 7):
        if start >= len(data):
            break
        byte = data[start]
        start += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, start
    raise ValueError('Invalid protobuf varint')


def _strings(data):
    start = 0
    while start < len(data):
        tag, start = _varint(data, start)
        wire_type = tag & 7
        if wire_type == 0:
            _, start = _varint(data, start)
            continue
        if wire_type == 2:
            size, start = _varint(data, start)
        elif wire_type == 1:
            size = 8
        elif wire_type == 5:
            size = 4
        else:
            raise ValueError('Unsupported protobuf wire type')
        end = start + size
        if end > len(data):
            raise ValueError('Truncated protobuf field')
        if wire_type == 2:
            yield tag >> 3, data[start:end]
        start = end


def read_metadata(model: bytes) -> dict[str, str]:
    # ONNX ModelProto field 14 is a repeated pair of UTF-8 strings.
    result = {}
    try:
        for field, payload in _strings(memoryview(model)):
            if field == 14 and len(payload) <= 32768:
                entry = {key: value.tobytes().decode('utf-8') for key, value in _strings(payload)}
                if 1 in entry and 2 in entry:
                    result[entry[1]] = entry[2]
    except (UnicodeError, ValueError):
        pass
    return result


def literal(value: str):
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError, RecursionError):
        return None


def class_names(value: str) -> tuple[str, ...]:
    names = literal(value)
    if isinstance(names, dict):
        names = [names.get(index, names.get(str(index))) for index in range(len(names))]
    if isinstance(names, (list, tuple)) and names and all(isinstance(name, str) and name.strip() for name in names):
        return tuple(name.strip()[:80] for name in names)
    return ()
