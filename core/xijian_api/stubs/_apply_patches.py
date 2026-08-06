#!/usr/bin/env python
"""Apply translation patches: {filename: {"insert_after": {lineno: [lines]}, "replace": {lineno: newline}}}"""
import sys, json, os, subprocess

PY = "/opt/anaconda3/envs/xijianBase/bin/python"

def apply_patch(path, patch):
    with open(path, encoding='utf-8') as f:
        lines = f.read().splitlines(keepends=True)
    insert_after = patch.get("insert_after", {})
    replace = patch.get("replace", {})
    if replace:
        for ln, newline in sorted(replace.items(), reverse=True):
            if ln < 1 or ln > len(lines):
                raise SystemExit(f"{path}: replace lineno {ln} out of range")
            lines[ln-1] = newline if newline.endswith('\n') else newline + '\n'
    if insert_after:
        # build new content
        out = []
        for i, line in enumerate(lines, 1):
            out.append(line)
            if i in insert_after:
                for ins in insert_after[i]:
                    out.append(ins if ins.endswith('\n') else ins + '\n')
        lines = out
    with open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    # py_compile check
    r = subprocess.run([PY, "-m", "py_compile", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"py_compile FAILED for {path}:\n{r.stderr}")
    return len(insert_after) + len(replace)

if __name__ == "__main__":
    # patches passed as python file(s)
    for p in sys.argv[1:]:
        ns = {}
        exec(open(p, encoding='utf-8').read(), ns)
        for fname, patch in ns["PATCHES"].items():
            n = apply_patch(fname, patch)
            print(f"OK {fname}: {n} edit points")
