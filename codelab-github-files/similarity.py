"""Lightweight C++ similarity review aid (NOT proof of copying).
A small lexical scanner splits source into tokens, correctly handling string/char literals and comments,
then token sequences are compared with difflib. Two scores are produced:
  token_score     - exact token sequence (identifiers kept)
  structure_score - identifiers and literals replaced by placeholders (survives variable renaming)"""
import difflib, re

KEYWORDS = set("""alignas alignof and asm auto bool break case catch char class const constexpr continue decltype default delete do
double else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept not nullptr
operator or private protected public register return short signed sizeof static struct switch template this throw true try typedef
typeid typename union unsigned using virtual void volatile while cin cout cerr endl string vector std""".split())
OPS3 = ("<<=", ">>=", "...", "->*")
OPS2 = ("<<", ">>", "<=", ">=", "==", "!=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "->", "::")
IDENT = re.compile(r"[A-Za-z_]\w*"); NUM = re.compile(r"\d[\w.']*(?:[eE][+-]?\d+)?")

def tokenize(src):
    """Return a list of (kind, text) tokens. kinds: id, kw, num, str, chr, op."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace(): i += 1; continue
        if c == "#":                       # preprocessor line (#include, #define ...) - skip, honouring line continuation
            while i < n and src[i] != "\n":
                if src[i] == "\\" and i + 1 < n: i += 1
                i += 1
            continue
        if src.startswith("//", i):
            while i < n and src[i] != "\n": i += 1
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        if c == '"' or c == "'":           # string / char literal with escapes; comment markers inside are just text
            q, j = c, i + 1
            while j < n and src[j] != q and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(("str" if q == '"' else "chr", src[i:j + 1])); i = j + 1; continue
        m = IDENT.match(src, i)
        if m:
            w = m.group(); out.append(("kw" if w in KEYWORDS else "id", w)); i = m.end(); continue
        m = NUM.match(src, i)
        if m: out.append(("num", m.group())); i = m.end(); continue
        for op in OPS3 + OPS2:
            if src.startswith(op, i): out.append(("op", op)); i += len(op); break
        else:
            out.append(("op", c)); i += 1
    return out

def _exact(toks): return [t for _, t in toks]
def _struct(toks): return [t if k in ("kw", "op") else k.upper() for k, t in toks]
def _ratio(a, b):
    if not a or not b: return 0.0
    a, b = a[:4000], b[:4000]
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()

def compare(code_a, code_b, min_tokens=25, template=None):
    """Return dict(token_score, structure_score, tokens_a, tokens_b, comparable, note)."""
    ta, tb = tokenize(code_a), tokenize(code_b)
    res = {"token_score": 0.0, "structure_score": 0.0, "tokens_a": len(ta), "tokens_b": len(tb), "comparable": True, "note": ""}
    if template is not None:
        tt = _exact(tokenize(template))
        if _exact(ta) == tt or _exact(tb) == tt:
            res.update(comparable=False, note="At least one submission is still the unmodified starter template."); return res
    if min(len(ta), len(tb)) < min_tokens:
        res.update(comparable=False, note=f"Too short to compare reliably (< {min_tokens} tokens)."); return res
    res["token_score"] = round(_ratio(_exact(ta), _exact(tb)), 4)
    res["structure_score"] = round(_ratio(_struct(ta), _struct(tb)), 4)
    return res

def flagged(res, threshold): return res["comparable"] and res["structure_score"] >= threshold
