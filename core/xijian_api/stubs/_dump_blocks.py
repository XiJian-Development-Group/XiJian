#!/usr/bin/env python
"""Group pure-English comments/docstrings into BLOCKS; skip blocks already bilingual."""
import re, sys, tokenize, io, ast

def has_cjk(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s))

def analyze(path):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines(keepends=True)
    # comment line numbers
    comment_lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                r0, r1 = tok.start, tok.end
                if r0[0] == r1[0]:
                    comment_lines.add(r0[0])
    except Exception as e:
        print(f"tokenize error: {e}", file=sys.stderr)
    # docstring ranges
    docstring_ranges = []
    try:
        tree = ast.parse(src)
    except Exception as e:
        print(f"ast error: {e}", file=sys.stderr)
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                ds = ast.get_docstring(node, clean=False)
                if ds is not None:
                    docstring_ranges.append((node.body[0].lineno, node.body[0].end_lineno))

    def is_directive(body):
        return bool(re.match(r'^(noqa|type: ignore|pragma|ruff|flake8|pyright|mypy)[:\s]', body))

    print(f"##### {path}")
    # ---- comment blocks ----
    c_lines = sorted(comment_lines)
    # group consecutive comment lines that are pure-English (skip directives/dividers)
    i = 0
    while i < len(c_lines):
        ln = c_lines[i]
        text = lines[ln-1].rstrip('\n')
        stripped = text.strip()
        body = stripped.lstrip('#').strip()
        if is_directive(body):
            i += 1; continue
        if re.match(r'^-{3,}$', body):
            i += 1; continue
        # collect block start
        block = [ln]
        j = i + 1
        while j < len(c_lines) and c_lines[j] == block[-1] + 1:
            block.append(c_lines[j]); j += 1
        # check if any line in block has CJK
        block_cjk = any(has_cjk(lines[l-1]) for l in block)
        if block_cjk:
            i = j; continue
        # print block
        print(f"  C-BLOCK L{block[0]}-{block[-1]}:")
        for l in block:
            print(f"    L{l}: {lines[l-1].rstrip()}")
        i = j
    # ---- docstrings ----
    for (s, e) in docstring_ranges:
        content = lines[s-1:e]
        pure_en = [l for l in range(s, e+1) if lines[l-1].strip() and not has_cjk(lines[l-1])]
        has_zh = any(has_cjk(lines[l-1]) for l in range(s, e+1))
        if has_zh:
            # still report if large portion is EN (fragments only)
            total_nonblank = sum(1 for l in range(s, e+1) if lines[l-1].strip())
            if pure_en and len(pure_en) / max(total_nonblank, 1) > 0.5 and len(pure_en) >= 4:
                print(f"  DS-PARTIAL L{s}-{e} (en {len(pure_en)}/{total_nonblank}):")
                for l in range(s, e+1):
                    if lines[l-1].strip():
                        print(f"    L{l}: {lines[l-1].rstrip()}")
            continue
        if pure_en:
            print(f"  DS-FULL L{s}-{e}:")
            for l in range(s, e+1):
                if lines[l-1].strip():
                    print(f"    L{l}: {lines[l-1].rstrip()}")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        analyze(p)
