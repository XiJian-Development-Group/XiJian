#!/usr/bin/env python
"""Dump pure-English comment/docstring lines for translation review."""
import re, sys, tokenize, io, ast, json

def has_cjk(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s))

def analyze(path):
    src = open(path, encoding='utf-8').read()
    lines = src.splitlines(keepends=True)
    comment_lines = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                r0, r1 = tok.start, tok.end
                if r0[0] == r1[0]:
                    comment_lines.add(r0[0])
    except Exception as e:
        print(f"tokenize error: {e}", file=sys.stderr)
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
                    start = node.body[0].lineno
                    end = node.body[0].end_lineno
                    docstring_ranges.append((start, end))
    out = {"file": path, "comments": [], "docstrings": []}
    # comments
    for ln in sorted(comment_lines):
        text = lines[ln-1].rstrip('\n')
        stripped = text.strip()
        body = stripped.lstrip('#').strip()
        # skip pure directives
        if re.match(r'^(noqa|type: ignore|pragma|ruff|flake8|pyright|mypy)[:\s]', body):
            continue
        # skip ASCII-art dividers (dashes only, or divider+identifier)
        if re.match(r'^-{3,}$', body):
            continue
        if re.fullmatch(r'-{3,}\s*[A-Za-z0-9_.\- ]*\s*-{3,}', body) and not has_cjk(body) and len(body) > 8:
            # divider like ---- character_models ----
            if re.fullmatch(r'-{3,}\s*[A-Za-z0-9_.\- ]+\s*-{3,}', body):
                continue
        if has_cjk(text):
            continue
        is_inline = not text.lstrip().startswith('#')
        out["comments"].append({"line": ln, "text": text, "inline": is_inline})
    # docstrings: report ranges and content of pure-English portions
    for (s, e) in docstring_ranges:
        content = lines[s-1:e]
        pure_en_lines = []
        for ln in range(s, e+1):
            t = lines[ln-1].rstrip('\n')
            if not t.strip():
                continue
            if has_cjk(t):
                continue
            pure_en_lines.append({"line": ln, "text": t})
        if pure_en_lines:
            out["docstrings"].append({
                "range": [s, e],
                "text": "".join(content),
                "pure_en": pure_en_lines,
            })
    return out

if __name__ == "__main__":
    for p in sys.argv[1:]:
        res = analyze(p)
        n_comments = len(res["comments"])
        n_ds = sum(len(d["pure_en"]) for d in res["docstrings"])
        print(f"##### {p}  (pure-EN comments: {n_comments}, docstring lines: {n_ds})")
        for c in res["comments"]:
            tag = "INLINE" if c["inline"] else "FULL  "
            print(f"  C {tag} L{c['line']}: {c['text']}")
        for d in res["docstrings"]:
            print(f"  DS L{d['range'][0]}-{d['range'][1]}:")
            for pl in d["pure_en"]:
                print(f"      L{pl['line']}: {pl['text']}")
