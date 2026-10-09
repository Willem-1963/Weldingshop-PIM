"""Persist explicitly authorized shop pack exceptions independently of portal data."""
import argparse,json,sqlite3,sys,math
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.suppliers.hub import supplier_database_path
from app.suppliers.certilas_order_units import load_pack_overrides

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--pack',action='append',required=True,help='SKU=kg');ap.add_argument('--reason',required=True);args=ap.parse_args()
 stamp=datetime.now(timezone.utc);path=supplier_database_path('certilas');rules={}
 with sqlite3.connect(path) as c:
  for spec in args.pack:
   sku,value=spec.split('=',1);weight=float(value.replace(',','.'))
   if not math.isfinite(weight) or weight<=0:raise ValueError('Ongeldig pakgewicht')
   row=c.execute('SELECT ean,kg_per_sales_unit FROM products WHERE sku=? AND source_present=1',(sku,)).fetchone()
   if not row or not row[0]:raise ValueError('Actueel PIM-artikel met EAN ontbreekt: '+sku)
   rules[sku]={'ean':str(row[0]),'kg_per_pack':weight,'reason':args.reason,'updated_at':stamp.isoformat()}
  before=load_pack_overrides(c)
  audit=Path(__file__).resolve().parents[1]/'data/audit'/('certilas-pack-overrides-'+stamp.strftime('%Y%m%dT%H%M%SZ')+'.json')
  audit.write_text(json.dumps({'before':before,'requested':rules},ensure_ascii=False,indent=2))
  c.execute('CREATE TABLE IF NOT EXISTS certilas_pack_overrides(sku TEXT PRIMARY KEY,rule_json TEXT NOT NULL)')
  for sku,rule in rules.items():
   c.execute('INSERT INTO certilas_pack_overrides VALUES (?,?) ON CONFLICT(sku) DO UPDATE SET rule_json=excluded.rule_json',(sku,json.dumps(rule,ensure_ascii=False)))
 print(json.dumps({'exceptions':rules,'audit':str(audit)},ensure_ascii=False))
if __name__=='__main__':main()
