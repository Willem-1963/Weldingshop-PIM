from __future__ import annotations
import argparse,sqlite3,json,math,hashlib,datetime,pathlib
from app.suppliers.hub import supplier_database_path

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def ensure(c):
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_policy_config(
  policy_key TEXT PRIMARY KEY,config_json TEXT NOT NULL,updated_by TEXT NOT NULL,
  decision_reason TEXT NOT NULL,updated_at TEXT NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_unit_policies(
  sku TEXT PRIMARY KEY,ean TEXT NOT NULL,physical_pack_kg REAL,
  portal_minimum_kg REAL,supplier_minimum_packs INTEGER NOT NULL,
  sales_mode TEXT NOT NULL,shop_packs_per_unit INTEGER NOT NULL,
  decision_source TEXT NOT NULL,decision_reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL,source_hash TEXT NOT NULL,
  policy_version INTEGER NOT NULL,evaluated_at TEXT NOT NULL,updated_at TEXT NOT NULL,
  CHECK(sales_mode IN ('single_pack','supplier_minimum','blocked')),
  CHECK(supplier_minimum_packs>=1),CHECK(shop_packs_per_unit>=1))''')
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_unit_policy_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,sku TEXT NOT NULL,old_policy_json TEXT,
  new_policy_json TEXT NOT NULL,change_reason TEXT NOT NULL,changed_by TEXT NOT NULL,
  changed_at TEXT NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_policy_config_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,policy_key TEXT NOT NULL,old_config_json TEXT,
  new_config_json TEXT NOT NULL,reason TEXT NOT NULL,changed_by TEXT NOT NULL,changed_at TEXT NOT NULL)''')
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_policy_overrides(
  sku TEXT PRIMARY KEY,sales_mode TEXT NOT NULL,shop_packs_per_unit INTEGER,
  reason TEXT NOT NULL,updated_by TEXT NOT NULL,updated_at TEXT NOT NULL,
  CHECK(sales_mode IN (\'single_pack\',\'supplier_minimum\',\'blocked\')))''')
 c.execute('''CREATE TABLE IF NOT EXISTS certilas_sales_policy_override_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,sku TEXT NOT NULL,old_override_json TEXT,
  new_override_json TEXT NOT NULL,reason TEXT NOT NULL,changed_by TEXT NOT NULL,changed_at TEXT NOT NULL)''')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--sales-evidence',required=True);ap.add_argument('--apply',action='store_true');args=ap.parse_args()
 db=supplier_database_path('certilas');now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 sales_doc=json.loads(pathlib.Path(args.sales_evidence).read_text());sales={r['sku']:r for r in sales_doc['rows']}
 default_config={'schema_version':1,'lookback_days':365,'minimum_distinct_paid_orders':2,'minimum_observed_units_vs_supplier_minimum_packs':1,'default_mode':'supplier_minimum','round_supplier_minimum_to_whole_physical_packs':True,'qualified_sales_mode':'single_pack','manual_override_precedence':True}
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row;ensure(c)
  stored=c.execute("select config_json from certilas_sales_policy_config where policy_key='default'").fetchone()
  config=json.loads(stored[0]) if stored else default_config
  config={**config,'sales_evidence_file':str(args.sales_evidence),'sales_evidence_created_at':sales_doc['created_at']}
  raw={r['sku']:json.loads(r['rule_json']) for r in c.execute('select * from certilas_order_units')}
  products={r['sku']:dict(r) for r in c.execute('select * from products')}
  overrides={r['sku']:json.loads(r['rule_json']) for r in c.execute('select * from certilas_pack_overrides')}
  sales_overrides={r['sku']:dict(r) for r in c.execute('select * from certilas_sales_policy_overrides')}
  planned=[]
  for sku,u in sorted(raw.items()):
   p=products.get(sku) or {};ov=overrides.get(sku);sov=sales_overrides.get(sku);sm=sales.get(sku)
   pack=float((ov or {}).get('kg_per_pack') or p.get('kg_per_sales_unit') or 0);portal=float(u.get('minimum_order_kg') or 0)
   supplier_packs=max(1,int(math.ceil((portal/pack)-1e-9))) if pack>0 and portal>0 else 1
   if sov:
    mode=sov['sales_mode'];shop_packs=int(sov['shop_packs_per_unit'] or (1 if mode=='single_pack' else supplier_packs));source='manual_sales_override';reason=sov['reason']
   elif pack<=0:
    mode='blocked';shop_packs=1;source='missing_physical_pack';reason='Geblokkeerd: fysiek verpakkingsgewicht ontbreekt.'
   elif ov:
    mode='single_pack';shop_packs=1;source='manual_pack_override';reason='Expliciete, herleidbare lokale packoverride blijft leidend.'
   elif sm and sm.get('qualified_single_pack'):
    mode='single_pack';shop_packs=1;source='sales_velocity';reason='Per pak: 365-dagenomloop voldoet aan de instelbare verkoopdrempels.'
   elif supplier_packs==1:
    mode='single_pack';shop_packs=1;source='whole_pack_rounding';reason='Portalminimum past binnen één volledige fysieke verpakking.'
   else:
    mode='supplier_minimum';shop_packs=supplier_packs;source='supplier_minimum';reason='Leveranciersminimum afgerond naar een geheel aantal fysieke verpakkingen om restvoorraad te voorkomen.'
   evidence={'portal_rule':u,'product':{'source_present':p.get('source_present'),'kg_per_sales_unit':p.get('kg_per_sales_unit'),'ean':p.get('ean'),'sale_price':p.get('sale_price'),'cost_price':p.get('cost_price')},'manual_pack_override':ov,'sales_velocity':sm,'policy_config':config,'approved_by':'Ramon Labudzik','approved_at':'2026-11-13T17:34:00+01:00'}
   if sov:evidence['manual_sales_override']=sov
   source_hash=hashlib.sha256(canonical(evidence).encode()).hexdigest();old=c.execute('select * from certilas_sales_unit_policies where sku=?',(sku,)).fetchone();version=(old['policy_version']+1) if old and old['source_hash']!=source_hash else (old['policy_version'] if old else 1)
   row={'sku':sku,'ean':str(u.get('ean') or p.get('ean') or ''),'physical_pack_kg':pack or None,'portal_minimum_kg':portal or None,'supplier_minimum_packs':supplier_packs,'sales_mode':mode,'shop_packs_per_unit':shop_packs,'decision_source':source,'decision_reason':reason,'evidence_json':canonical(evidence),'source_hash':source_hash,'policy_version':version,'evaluated_at':now,'updated_at':now}
   planned.append(row)
  summary={'apply':args.apply,'rows':len(planned),'single_pack':sum(r['sales_mode']=='single_pack' for r in planned),'supplier_minimum':sum(r['sales_mode']=='supplier_minimum' for r in planned),'blocked':sum(r['sales_mode']=='blocked' for r in planned),'sales_velocity_single_pack':[r['sku'] for r in planned if r['decision_source']=='sales_velocity'],'changed':0}
  if args.apply:
   c.execute('insert into certilas_sales_policy_config values (?,?,?,?,?) on conflict(policy_key) do nothing',('default',canonical(default_config),'Ramon Labudzik','Duurzaam en herleidbaar beleid: leverancierminimum in hele fysieke pakken; hardlopers per pak.',now))
   cols=['sku','ean','physical_pack_kg','portal_minimum_kg','supplier_minimum_packs','sales_mode','shop_packs_per_unit','decision_source','decision_reason','evidence_json','source_hash','policy_version','evaluated_at','updated_at']
   for row in planned:
    old=c.execute('select * from certilas_sales_unit_policies where sku=?',(row['sku'],)).fetchone();oldj=canonical(dict(old)) if old else None
    material=not old or any(old[k]!=row[k] for k in ('ean','physical_pack_kg','portal_minimum_kg','supplier_minimum_packs','sales_mode','shop_packs_per_unit','decision_source','source_hash'))
    if material:
     summary['changed']+=1;c.execute('insert into certilas_sales_unit_policy_history(sku,old_policy_json,new_policy_json,change_reason,changed_by,changed_at) values (?,?,?,?,?,?)',(row['sku'],oldj,canonical(row),row['decision_reason'],'Ramon Labudzik',now))
    c.execute('insert into certilas_sales_unit_policies('+','.join(cols)+') values ('+','.join('?' for _ in cols)+') on conflict(sku) do update set '+','.join(f'{k}=excluded.{k}' for k in cols[1:]),tuple(row[k] for k in cols))
   c.commit();summary['integrity']=c.execute('pragma integrity_check').fetchone()[0]
  print(json.dumps({'summary':summary,'policies':planned},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
