"""Persist explicit multi-pack labels in Certilas family data."""
from __future__ import annotations
import argparse,json,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.suppliers.certilas_order_units import sales_unit_packaging_label
from app.suppliers.hub import supplier_database_path


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args()
 path=supplier_database_path('certilas');now=datetime.now(timezone.utc).isoformat();changes=[]
 with sqlite3.connect(path) as c:
  c.row_factory=sqlite3.Row
  policies={r['sku']:dict(r) for r in c.execute("select sku,physical_pack_kg,shop_packs_per_unit,source_hash from certilas_sales_unit_policies where sales_mode='supplier_minimum' and shop_packs_per_unit>1")}
  families=list(c.execute('select family_key,family_json from product_families'))
  for row in families:
   family=json.loads(row['family_json'] or '{}');family_changed=False
   for item in family.get('variants') or []:
    sku=str(item.get('sku') or '');p=policies.get(sku)
    if not p:continue
    rule={'local_sales_policy':True,**p}
    old=str(item.get('packaging_option_label') or item.get('package_label') or '').strip()
    new=sales_unit_packaging_label(old,rule)
    diameter=str(item.get('diameter_option_label') or '').strip()
    old_title=str(item.get('variant_title') or '').strip()
    new_title=' · '.join(filter(None,(diameter,new)))
    if old==new and old_title==new_title:continue
    changes.append({'family_key':row['family_key'],'sku':sku,'old_label':old,'new_label':new,'old_title':old_title,'new_title':new_title,'source_hash':p['source_hash']})
    item['packaging_option_label']=new;item['variant_title']=new_title;family_changed=True
   if family_changed and args.apply:
    c.execute('update product_families set family_json=? where family_key=?',(json.dumps(family,ensure_ascii=False),row['family_key']))
  if args.apply:
   c.execute('''create table if not exists certilas_variant_label_history(
    id integer primary key autoincrement,family_key text not null,sku text not null,
    old_label text not null,new_label text not null,old_title text not null,new_title text not null,
    source_policy_hash text not null,changed_at text not null)''')
   for x in changes:
    c.execute('insert into certilas_variant_label_history(family_key,sku,old_label,new_label,old_title,new_title,source_policy_hash,changed_at) values(?,?,?,?,?,?,?,?)',(x['family_key'],x['sku'],x['old_label'],x['new_label'],x['old_title'],x['new_title'],x['source_hash'],now))
    c.execute('update product_family_variants set variant_title=? where family_key=? and sku=?',(x['new_title'],x['family_key'],x['sku']))
   c.commit()
 print(json.dumps({'mode':'apply' if args.apply else 'plan','changes':len(changes),'rows':changes},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
