"""WCAG contrast check of token pairs.  python tests/ui/contrast.py"""
import re, sys
def lum(h):
    h=h.lstrip('#'); r,g,b=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    f=lambda c: c/12.92 if c<=.03928 else ((c+.055)/1.055)**2.4
    return .2126*f(r)+.7152*f(g)+.0722*f(b)
def cr(a,b):
    la,lb=sorted([lum(a),lum(b)],reverse=True); return (la+.05)/(lb+.05)
def themes():
    css=open("static/index.html").read()
    out={}
    for name,sel in (("light",":root{"),("dark",':root[data-theme="dark"]{')):
        i=css.find(sel)
        if i<0: continue
        blk=css[i:css.index("}",i)]
        out[name]=dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})",blk))
    return out
PAIRS=[("text","bg",4.5),("text","surface",4.5),("muted","bg",4.5),("muted","surface",4.5),("muted","surface-2",4.5),("dim","bg",4.5),("dim","surface",4.5),
       ("accent-ink","accent",4.5),("good","surface",4.5),("bad","surface",4.5),("warn","surface",4.5),("accent","surface",4.5),("line-strong","surface",3.0),("pos-qb","surface",4.5),("pos-rb","surface",4.5),("pos-wr","surface",4.5),("pos-te","surface",4.5),("pos-k","surface",4.5),("pos-dst","surface",4.5)]
fail=0
for tn,t in themes().items():
    base=themes()["light"]
    for a,b,need in PAIRS:
        ca=t.get(a) or base.get(a); cb=t.get(b) or base.get(b)
        if not ca or not cb: continue
        r=cr(ca,cb); ok=r>=need; fail+=not ok
        print(f"{tn:5} {a:11} on {b:9} {r:5.2f} (need {need}) {'ok' if ok else 'FAIL'}")
sys.exit(1 if fail else 0)
