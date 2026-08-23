#!/usr/bin/env python3
"""Validate module XML <record> field names against real Odoo 19 core models.
Resolves both classic (_inherit) and delegation (_inherits) inheritance."""
import re, os, glob, collections
import xml.etree.ElementTree as ET

SRC="/tmp/o19"; MOD="/home/claude/work/out/asset_management"
fields_of=collections.defaultdict(set)
parents=collections.defaultdict(set)

class_re=re.compile(r"^class\s+\w+\(")
name_re =re.compile(r"^\s+_name\s*=\s*['\"]([\w.]+)['\"]",re.M)
inh1_re =re.compile(r"^\s+_inherit\s*=\s*['\"]([\w.]+)['\"]",re.M)
inhL_re =re.compile(r"^\s+_inherit\s*=\s*\[(.+?)\]",re.M|re.S)
inhs_re =re.compile(r"^\s+_inherits\s*=\s*\{(.+?)\}",re.M|re.S)
field_re=re.compile(r"^\s{4}(\w+)\s*=\s*fields\.\w+",re.M)

for path in glob.glob(os.path.join(SRC,"*.py")):
    lines=open(path,encoding="utf-8",errors="replace").read().split("\n")
    st=[i for i,l in enumerate(lines) if class_re.match(l)]+[len(lines)]
    for a,b in zip(st,st[1:]):
        blk="\n".join(lines[a:b])
        nm=name_re.findall(blk)
        inh=inh1_re.findall(blk)+re.findall(r"['\"]([\w.]+)['\"]",
             (inhL_re.findall(blk) or [""])[0])
        model = nm[0] if nm else (inh[0] if inh else None)
        if not model: continue
        fields_of[model] |= set(field_re.findall(blk))
        # classic inheritance: this model inherits parents' fields
        for p in inh:
            if p != model: parents[model].add(p)
        # delegation inheritance
        for d in inhs_re.findall(blk):
            parents[model] |= set(re.findall(r"['\"]([\w.]+)['\"]\s*:",d))

def resolve(m,seen=None):
    seen=seen or set()
    if m in seen: return set()
    seen.add(m)
    out=set(fields_of.get(m,()))
    for p in parents.get(m,()): out|=resolve(p,seen)
    return out

BASE={"id","display_name","create_date","write_date","create_uid","write_uid"}
resolved={m:resolve(m)|BASE for m in set(list(fields_of)+list(parents))}

print(f"Odoo 19 core models resolved: {len(resolved)}")
for m in ["ir.rule","ir.cron","ir.model.access","ir.ui.menu","ir.ui.view","res.groups",
          "res.groups.privilege","ir.module.category","ir.actions.act_window",
          "ir.actions.client","ir.actions.server","mail.template","ir.sequence"]:
    print(f"   {m:26} {len(resolved.get(m,()))} fields")

problems=[];checked=0
for dp,dn,fn in os.walk(MOD):
    dn[:]=[d for d in dn if d not in("__pycache__","lib")]
    for f in sorted(fn):
        if not f.endswith(".xml"): continue
        p=os.path.join(dp,f); rel=os.path.relpath(p,MOD)
        try: tree=ET.parse(p)
        except ET.ParseError as e:
            problems.append((rel,"-","XML PARSE ERROR",str(e))); continue
        for rec in tree.iter("record"):
            mdl=rec.get("model")
            if not mdl or mdl not in resolved: continue
            for fld in rec.findall("field"):
                nm=fld.get("name")
                if not nm: continue
                checked+=1
                if nm not in resolved[mdl]:
                    problems.append((rel,rec.get("id","?"),mdl,nm))

print(f"\ncore-model <field> references checked: {checked}")
print(f"PROBLEMS: {len(problems)}")
for rel,rid,mdl,nm in problems:
    print(f"  {rel} id={rid}\n      {mdl}: UNKNOWN FIELD '{nm}'")
if not problems:
    print("  none - every core-model field reference is valid in Odoo 19.")
