from __future__ import annotations
import argparse,sqlite3,json,datetime
from app.suppliers.hub import supplier_database_path
from scripts.rebuild_certilas_sales_policies import ensure,canonical

def scalar(v):
 try:return json.loads(v)
 except json.JSONDecodeError:return v

def main():
 ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='action',required=True)
 o=sub.add_parser('override');o.add_argument('--sku',required=True);o.add_argument('--mode',choices=['single_pack','supplier_minimum','blocked'],required=True);o.add_argument('--packs',type=int);o.add_argument('--reason',required=True);o.add_argument('--updated-by',default='Ramon Labudzik')
 c=sub.add_parser('config');c.add_argument('--set',action='append',required=True);c.add_argument('--reason',required=True);c.add_argument('--updated-by',default='Ramon Labudzik')
 args=ap.parse_args();now=datetime.datetime.now(datetime.timezone.utc).isoformat();db=supplier_database_path('certilas')
 with sqlite3.connect(db) as con:
  con.row_factory=sqlite3.Row;ensure(con)
  if args.action=='override':
   if args.packs is not None and args.packs<=0:raise ValueError('packs moet positief zijn')
   if not con.execute('select 1 from products where sku=?',(args.sku,)).fetchone():raise ValueError('onbekende SKU')
   old=con.execute('select * from certilas_sales_policy_overrides where sku=?',(args.sku,)).fetchone();new={'sku':args.sku,'sales_mode':args.mode,'shop_packs_per_unit':args.packs,'reason':args.reason,'updated_by':args.updated_by,'updated_at':now}
   con.execute('insert into certilas_sales_policy_override_history(sku,old_override_json,new_override_json,reason,changed_by,changed_at) values(?,?,?,?,?,?)',(args.sku,canonical(dict(old)) if old else None,canonical(new),args.reason,args.updated_by,now));con.execute('insert into certilas_sales_policy_overrides values(?,?,?,?,?,?) on conflict(sku) do update set sales_mode=excluded.sales_mode,shop_packs_per_unit=excluded.shop_packs_per_unit,reason=excluded.reason,updated_by=excluded.updated_by,updated_at=excluded.updated_at',tuple(new.values()))
   result=new
  else:
   old=con.execute("select * from certilas_sales_policy_config where policy_key='default'").fetchone();cfg=json.loads(old['config_json']) if old else {}
   for item in args.set:
    if '=' not in item:raise ValueError('--set vereist sleutel=waarde')
    k,v=item.split('=',1);cfg[k]=scalar(v)
   con.execute('insert into certilas_sales_policy_config_history(policy_key,old_config_json,new_config_json,reason,changed_by,changed_at) values(?,?,?,?,?,?)',('default',old['config_json'] if old else None,canonical(cfg),args.reason,args.updated_by,now));con.execute('insert into certilas_sales_policy_config values(?,?,?,?,?) on conflict(policy_key) do update set config_json=excluded.config_json,updated_by=excluded.updated_by,decision_reason=excluded.decision_reason,updated_at=excluded.updated_at',('default',canonical(cfg),args.updated_by,args.reason,now));result=cfg
  con.commit();print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
