from __future__ import annotations
import argparse,json,sqlite3,sys,math,datetime,pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from app.suppliers.hub import supplier_database_path
from app.suppliers.discounts import apply_sales_prices,_calculated_sales_price
TARGETS={'10022':13.2,'10454':14.4,'130432':8.0}

def fmt(v):return f'{v:.10f}'.rstrip('0').rstrip('.').replace('.',',')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');args=ap.parse_args();db=supplier_database_path('certilas');now=datetime.datetime.now(datetime.timezone.utc).isoformat();planned=[]
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row
  portal={r['sku']:json.loads(r['rule_json']) for r in c.execute('select * from certilas_order_units')}
  before={r['sku']:dict(r) for r in c.execute('select * from products where sku in (?,?,?)',tuple(TARGETS))}
  for sku,total in TARGETS.items():
   p=before[sku];raw=json.loads(p['raw_data_json'] or '{}');bundle=float(str(raw.get('KG Ceweld Bundle')).replace(',','.'));rule=portal[sku]
   if not math.isclose(bundle,total,abs_tol=1e-6) or not math.isclose(float(rule['minimum_order_kg']),total,abs_tol=1e-6) or str(rule['ean'])!=str(p['ean']):raise ValueError('Bronbewijs wijkt af: '+sku)
   pack=total/6;net=float(p['net_purchase_price_per_kg']);gross=float(p['gross_purchase_price_per_kg']);cost=net*pack;sale=_calculated_sales_price(gross*pack,cost,20,'max_discount_over_discount')
   raw['source_reported_KG Ceweld Unit']=raw.get('KG Ceweld Unit');raw['KG Ceweld Unit']=fmt(pack);raw['pack_override_proof']={'rule':'electrode_outer_case_six_packs','case_packs':6,'case_weight_kg':total,'physical_pack_kg':pack,'confirmed_by':'Ramon Labudzik','confirmed_at':'2026-11-13T20:02:00+01:00','portal_cart_verified':True}
   planned.append({'sku':sku,'ean':p['ean'],'old_pack_kg':p['kg_per_sales_unit'],'new_pack_kg':pack,'case_packs':6,'case_weight_kg':total,'old_cost':p['cost_price'],'new_cost':cost,'old_sale':p['sale_price'],'new_sale':sale,'raw':raw})
  if args.apply:
   c.execute('''create table if not exists certilas_overbox_pack_history(id integer primary key autoincrement,sku text not null,ean text not null,old_pack_kg real,new_pack_kg real not null,case_packs integer not null,case_weight_kg real not null,old_cost real,new_cost real not null,old_sale real,new_sale real,proof_json text not null,changed_by text not null,changed_at text not null)''')
   for x in planned:
    c.execute('update products set kg_per_purchase_unit=?,kg_per_sales_unit=?,purchase_units_per_sales_unit=1,cost_price=?,raw_data_json=?,updated_at=? where sku=?',(x['new_pack_kg'],x['new_pack_kg'],x['new_cost'],json.dumps(x['raw'],ensure_ascii=False),now,x['sku']))
    c.execute('insert into certilas_overbox_pack_history(sku,ean,old_pack_kg,new_pack_kg,case_packs,case_weight_kg,old_cost,new_cost,old_sale,new_sale,proof_json,changed_by,changed_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['sku'],x['ean'],x['old_pack_kg'],x['new_pack_kg'],6,x['case_weight_kg'],x['old_cost'],x['new_cost'],x['old_sale'],x['new_sale'],json.dumps(x['raw']['pack_override_proof'],ensure_ascii=False),'Ramon Labudzik',now))
   c.commit()
 if args.apply:
  apply_sales_prices('certilas')
  with sqlite3.connect(db) as c:
   c.row_factory=sqlite3.Row;after={r['sku']:dict(r) for r in c.execute('select * from products where sku in (?,?,?)',tuple(TARGETS))}
   for family_key,family_json in c.execute('select family_key,family_json from product_families').fetchall():
    family=json.loads(family_json or '{}');changed=False
    for item in family.get('variants') or []:
     sku=item.get('sku');p=after.get(sku)
     if not p:continue
     for k in ('sale_price','cost_price','raw_data_json','kg_per_purchase_unit','kg_per_sales_unit','purchase_units_per_sales_unit'):
      item[k]=p[k]
     item['package_weight']=p['kg_per_sales_unit'];item['package_weight_label']=fmt(p['kg_per_sales_unit'])+' kg';changed=True
    if changed:c.execute('update product_families set family_json=? where family_key=?',(json.dumps(family,ensure_ascii=False),family_key))
   c.commit();integrity=c.execute('pragma integrity_check').fetchone()[0]
  report={'mode':'apply','planned':[{k:v for k,v in x.items() if k!='raw'} for x in planned],'after':{sku:{k:p[k] for k in ('kg_per_purchase_unit','kg_per_sales_unit','cost_price','sale_price')} for sku,p in after.items()},'integrity':integrity}
 else:report={'mode':'plan','planned':[{k:v for k,v in x.items() if k!='raw'} for x in planned]}
 out=pathlib.Path('data/audit')/('certilas-electrode-overbox-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2));report['audit']=str(out);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
