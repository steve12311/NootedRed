"""读取生成头中的 constexpr 数组；只覆盖本仓库生成器的固定格式。

生成头由 tools/build_* 产生，格式稳定：namespace、`inline constexpr` 数组、
`sizeof(Name)` 与 `sizeof("path")`。这里不实现通用 C++ 解析器，解析失败即报错，
避免静默跳过导致 Manifest 与生成数据不一致。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Ident:
    """生成头中引用的另一个数组名（或无法静态求值的表达式）。"""

    name: str


@dataclass(frozen=True)
class SizeOf:
    """sizeof(ArrayName) 形式的长度引用。"""

    name: str


@dataclass
class CppArray:
    namespace: str
    ctype: str
    name: str
    items: object  # bytes 或 list[list[value]]

    @property
    def qualified(self) -> str:
        return f'{self.namespace}::{self.name}' if self.namespace else self.name

    @property
    def count(self) -> int:
        if isinstance(self.items, bytes):
            return len(self.items)
        return len(self.items)


@dataclass
class HeaderData:
    path: Path
    arrays: dict[str, CppArray] = field(default_factory=dict)
    scalars: dict[str, int] = field(default_factory=dict)
    strings: dict[str, str] = field(default_factory=dict)

    def array(self, namespace: str, name: str) -> CppArray:
        key = f'{namespace}::{name}' if namespace else name
        if key not in self.arrays:
            raise KeyError(f'{self.path.name}: 缺少 {key}')
        return self.arrays[key]

    def maybe_array(self, namespace: str, name: str) -> CppArray | None:
        key = f'{namespace}::{name}' if namespace else name
        return self.arrays.get(key)

    def byte_array(self, namespace: str, name: str) -> bytes:
        array = self.array(namespace, name)
        if not isinstance(array.items, bytes):
            raise TypeError(f'{self.path.name}: {array.qualified} 不是字节数组')
        return array.items

    def scalar(self, namespace: str, name: str) -> int:
        key = f'{namespace}::{name}' if namespace else name
        if key not in self.scalars:
            raise KeyError(f'{self.path.name}: 缺少标量 {key}')
        return self.scalars[key]

    def string(self, namespace: str, name: str) -> str:
        key = f'{namespace}::{name}' if namespace else name
        if key not in self.strings:
            raise KeyError(f'{self.path.name}: 缺少字符串 {key}')
        return self.strings[key]


_ARRAY = re.compile(
    r'(?:inline\s+)?constexpr\s+([A-Za-z_][\w:]*)\s+([A-Za-z_]\w*)\s*\[\s*\]\s*=\s*\{'
)
_SCALAR = re.compile(
    r'(?:inline\s+)?constexpr\s+(UInt8|UInt32|UInt64)\s+([A-Za-z_]\w*)\s*=\s*([^;]+);'
)
_CHAR = re.compile(r'(?:inline\s+)?constexpr\s+char\s+([A-Za-z_]\w*)\s*\[\s*\]\s*=\s*"([^"]*)"\s*;')


def _extract_initializer(text: str, start: int) -> tuple[str, int]:
    """从 start 处的 '{' 提取配对的花括号内容，跳过字符串字面量。"""
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise ValueError('未闭合的数组初始化')


def _split_top(text: str, separator: str = ',') -> list[str]:
    parts: list[str] = []
    depth = 0
    in_string = False
    escape = False
    start = 0
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in '({[':
            depth += 1
        elif char in ')}]':
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _value(token: str):
    token = token.strip()
    if not token:
        raise ValueError('空字段')
    match = re.fullmatch(r'(0x[0-9A-Fa-f]+|\d+)(?:ULL|UL|U|L)?', token)
    if match:
        return int(match.group(1), 0)
    match = re.fullmatch(r'sizeof\s*\(\s*"([^"]*)"\s*\)', token)
    if match:
        return len(match.group(1)) + 1
    match = re.fullmatch(r'sizeof\s*\(\s*([A-Za-z_]\w*)\s*\)', token)
    if match:
        return SizeOf(match.group(1))
    if token == 'true':
        return True
    if token == 'false':
        return False
    if token == 'nullptr':
        return None
    if token.startswith('"') and token.endswith('"'):
        return ast.literal_eval(token)
    # 生成器中的函数数组/设备表会引用带下标的表达式；保留名字供调用方解析。
    if re.fullmatch(r'[A-Za-z_]\w*(?:\[[^\]]*\])?(?:\.[A-Za-z_]\w*)?', token):
        return Ident(token)
    raise ValueError(f'无法解析的字段: {token}')


def _parse_struct_item(item: str):
    item = item.strip()
    if not (item.startswith('{') and item.endswith('}')):
        return _value(item)
    fields = _split_top(item[1:-1])
    return [_value(field) for field in fields]


def _namespace_regions(text: str) -> list[tuple[str, int, int]]:
    """按花括号深度确定命名空间范围，避免把函数体或数组的 '}' 当成命名空间结束。"""
    regions: list[tuple[str, int, int]] = []
    stack: list[tuple[str, int, int]] = []  # (name, start_line, outer_depth)
    depth = 0
    line = 0
    index = 0
    pending: tuple[str, int, int] | None = None
    in_string = in_line_comment = in_block_comment = False
    escape = False
    length = len(text)
    while index < length:
        char = text[index]
        if char == '\n':
            line += 1
            in_line_comment = False
            index += 1
            continue
        if in_line_comment:
            index += 1
            continue
        if in_block_comment:
            if char == '*' and index + 1 < length and text[index + 1] == '/':
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == '/' and index + 1 < length and text[index + 1] == '/':
            in_line_comment = True
            index += 2
            continue
        if char == '/' and index + 1 < length and text[index + 1] == '*':
            in_block_comment = True
            index += 2
            continue
        line_start = text.rfind('\n', 0, index) + 1
        if (not text[line_start:index].strip() and text.startswith('namespace', index)
            and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] == '_'))):
            match = re.match(r'namespace\s+([A-Za-z_]\w*)', text[index:])
            if match:
                pending = (match.group(1), line, depth)
                index += match.end()
                continue
        if char == '{':
            if pending is not None:
                stack.append(pending)
                pending = None
            depth += 1
        elif char == '}':
            if stack and depth == stack[-1][2] + 1:
                name, start_line, _ = stack.pop()
                prefix = '::'.join([entry[0] for entry in stack] + [name])
                regions.append((prefix, start_line, line))
            depth -= 1
        index += 1
    return regions


def _namespace_at(regions: list[tuple[str, int, int]], line: int) -> str:
    matches = [region for region in regions if region[1] <= line <= region[2]]
    if not matches:
        return ''
    # 嵌套命名空间取最内层（起始行最大）。
    return max(matches, key=lambda region: region[1])[0]


def parse(path: Path) -> HeaderData:
    text = path.read_text()
    regions = _namespace_regions(text)
    data = HeaderData(path=path)

    for match in _ARRAY.finditer(text):
        ctype, name = match.group(1), match.group(2)
        line = text.count('\n', 0, match.start())
        namespace = _namespace_at(regions, line)
        body, _ = _extract_initializer(text, match.end() - 1)
        if ctype == 'UInt8':
            items = bytes(int(token, 0) for token in _split_top(body))
        elif ctype == 'char':
            items = ast.literal_eval('"' + body + '"')
        else:
            items = [_parse_struct_item(item) for item in _split_top(body)]
        array = CppArray(namespace=namespace, ctype=ctype, name=name, items=items)
        if array.qualified in data.arrays:
            raise ValueError(f'{path.name}: 重复数组 {array.qualified}')
        data.arrays[array.qualified] = array

    for match in _CHAR.finditer(text):
        name, value = match.group(1), match.group(2)
        line = text.count('\n', 0, match.start())
        namespace = _namespace_at(regions, line)
        key = f'{namespace}::{name}' if namespace else name
        data.strings[key] = value

    for match in _SCALAR.finditer(text):
        ctype, name, value = match.group(1), match.group(2), match.group(3).strip()
        line = text.count('\n', 0, match.start())
        namespace = _namespace_at(regions, line)
        key = f'{namespace}::{name}' if namespace else name
        data.scalars[key] = int(re.sub(r'(?:ULL|UL|U|L)$', '', value), 0)

    return data
