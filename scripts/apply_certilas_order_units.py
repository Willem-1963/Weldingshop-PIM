"""Plan/apply verified Certilas minimum sale units to existing Shopify variants."""
from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.suppliers.hub import supplier_database_path
from app.suppliers.certilas_order_units import load_order_units,verified_order_unit
from app.shopify.sync import _price_only_variant_rows,_certilas_bundle_metafields
from app.shopify.client import ShopifyClient

ROOT=Path(__file__).resolve().parents[1]
AUDIT=ROOT/'data/audit'
QUERY='''query($after:String){products(first:25,after:$after,query:"vendor:Certilas"){
 pageInfo{hasNextPage endCursor} nodes{id title handle status options{name values}
 metafields(first:30,namespace:"custom"){nodes{key type value}}
 variants(first:100){pageInfo{hasNextPage} nodes{id sku barcode price compareAtPrice inventoryQuantity
 selectedOptions{name value} inventoryItem{id unitCost{amount} measurement{weight{value unit}}}
 metafields(first:30,namespace:"custom"){nodes{key type value}}}}}}}'''
UPDATE='''mutation($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
 productVariantsBulkUpdate(productId:$productId,variants:$variants){
 productVariants{id sku price} userErrors{field message code}}}'''

def snapshot(client):
 products=[];after=None
 while True:
  r=client.graphql(QUERY,{'after':after})['products'];products.extend(r['nodes'])
  if any(p['variants']['pageInfo']['hasNextPage'] for p in r['nodes']):raise ValueError('Variant pagination required')
  if not r['pageInfo']['hasNextPage']:break
  after=r['pageInfo']['endCursor']
 return products

def store_rules(path,units):
 with sqlite3.connect(path) as c:
  c.execute('CREATE TABLE IF NOT EXISTS certilas_order_units(sku TEXT PRIMARY KEY,rule_json TEXT NOT NULL)')
  for sku,rule in units.items():
   r=c.execute('SELECT ean FROM products WHERE sku=?',(sku,)).fetchone()
   if not r or str(r[0])!=str(rule['ean']):continue
   c.execute('INSERT INTO certilas_order_units VALUES (?,?) ON CONFLICT(sku) DO UPDATE SET rule_json=excluded.rule_json',
             (sku,json.dumps(rule,ensure_ascii=False)))

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');ap.add_argument('--allow-partial',action='store_true');args=ap.parse_args()
 units=json.loads((AUDIT/'certilas-order-units-20261009.json').read_text())['units']
 for name in ['certilas-order-unit-fallbacks-20261009.json','certilas-order-unit-final-20261009.json']:
  fallback=AUDIT/name
  if fallback.exists():
   for sku,rule in json.loads(fallback.read_text())['units'].items():
    if sku in units and any(units[sku][k]!=rule[k] for k in ('ean','minimum_order_kg','quantity_step')):raise ValueError('Tegenstrijdige portalregels: '+sku)
    units[sku]=rule
 client=ShopifyClient.from_settings();products=snapshot(client)
 stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');run=AUDIT/('certilas-minimum-order-'+stamp);run.mkdir(parents=True)
 (run/'shopify-before.json').write_text(json.dumps(products,ensure_ascii=False,indent=2))
 path=supplier_database_path('certilas')
 with sqlite3.connect(path) as c:
  c.row_factory=sqlite3.Row
  pim={r['sku']:dict(r) for r in c.execute('SELECT * FROM products WHERE source_present=1')}
 matches={};source=[];missing=[];changes=[];fields=[];deletions=[]
 for prod in products:
  for v in prod['variants']['nodes']:
   sku=v['sku'];p=pim.get(sku);rule=units.get(sku)
   if not p or not rule:
    missing.append({'sku':sku,'title':prod['title'],'reason':'Geen gecontroleerde portalregel' if p else 'Niet in actieve PIM-bron'})
    continue
   p.update(_supplier_slug='certilas',_certilas_order_unit=rule)
   try:verified_order_unit(p)
   except ValueError as e:
    missing.append({'sku':sku,'title':prod['title'],'reason':str(e)});continue
   if str(v.get('barcode') or '')!=str(p.get('ean') or ''):
    missing.append({'sku':sku,'title':prod['title'],'reason':'Shopify EAN wijkt af van portal/PIM'});continue
   matches[sku.upper()]={'product':prod,'variant':v};source.append(p)
 rows=_price_only_variant_rows(source,matches)
 for row in rows:
  for v in row['variants']:
   entry=next(m for m in matches.values() if m['variant']['id']==v['id']);before=entry['variant'];sku=before['sku'];p=pim[sku];rule=units[sku]
   fields.extend(dict(f,ownerId=v['id']) for f in v.pop('metafields',[]))
   for f in before['metafields']['nodes']:
    if f['key'] in {'verpakkingen_per_bundel','custom_minimale_afname'}:
     deletions.append(dict(ownerId=v['id'],namespace='custom',key=f['key']))
   changes.append({'sku':sku,'old_price':before['price'],'new_price':v.get('price'),
    'minimum_order_kg':rule['minimum_order_kg'],'cost':v.get('inventoryItem',{}).get('cost'),
    'old_inventory_quantity':before['inventoryQuantity']})
  for f in matches[next(p['sku'].upper() for p in source if matches[p['sku'].upper()]['product']['id']==row['productId'])]['product']['metafields']['nodes']:
   if f['key'] in {'minimale_afname','custom_minimale_afname'}:
    deletions.append(dict(ownerId=row['productId'],namespace='custom',key=f['key']))
 deletions=list({(d['ownerId'],d['key']):d for d in deletions}.values())
 report=dict(mode='apply' if args.apply else 'plan',matched=len(source),site_total=sum(len(p['variants']['nodes']) for p in products),
   missing=missing,changes=changes,rows=rows,metafields=fields,deletions=deletions)
 (run/'plan.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps({'run':str(run),'matched':len(source),'total':report['site_total'],'missing':len(missing),
 'examples':[x for x in changes if x['sku'] in ['10001','10022','50620']]}),flush=True)
 if not args.apply:return
 if missing and not args.allow_partial:raise ValueError('Niet alle sitevarianten zijn gecontroleerd; plan is opgeslagen.')
 # Back up SQLite before persisting independently of incoming price lists.
 with sqlite3.connect(path) as c,sqlite3.connect(run/'pim-before.sqlite') as backup:c.backup(backup)
 store_rules(path,units)
 definitions=client.graphql('query {metafieldDefinitions(first:250,ownerType:PRODUCTVARIANT){nodes{namespace key type{name}}}}')['metafieldDefinitions']['nodes']
 known={d['key']:d['type']['name'] for d in definitions if d['namespace']=='custom'}
 for f in fields:
  if f['key'] not in known:
   definition={'name':f['key'].replace('_',' ').capitalize(),'namespace':'custom','key':f['key'],
     'type':f['type'],'ownerType':'PRODUCTVARIANT','description':'Certilas verkoop- en inkoopgegevens uit de PIM'}
   result=client.graphql('''mutation($definition:MetafieldDefinitionInput!){metafieldDefinitionCreate(definition:$definition){createdDefinition{id key} userErrors{field message code}}}''',{'definition':definition})['metafieldDefinitionCreate']
   if result['userErrors']:raise RuntimeError(str(result['userErrors']))
   known[f['key']]=f['type']
  if known[f['key']]!=f['type']:raise ValueError('Velddefinitie wijkt af: '+f['key'])
 old_by_id={v['id']:v for prod in products for v in prod['variants']['nodes']}
 def variant_changed(v):
  old=old_by_id[v['id']];inv=v.get('inventoryItem',{});old_inv=old['inventoryItem']
  if v.get('price') is not None and float(old['price'])!=float(v['price']):return True
  if inv.get('cost') is not None and (not old_inv.get('unitCost') or float(old_inv['unitCost']['amount'])!=float(inv['cost'])):return True
  weight=inv.get('measurement',{}).get('weight');previous=old_inv.get('measurement',{}).get('weight')
  return weight and (not previous or previous['unit']!=weight['unit'] or abs(previous['value']-weight['value'])>1e-6)
 write_rows=[dict(productId=r['productId'],variants=[v for v in r['variants'] if variant_changed(v)]) for r in rows]
 write_rows=[r for r in write_rows if r['variants']]
 print('Varianten met gewijzigde prijs/kostprijs/gewicht: '+str(sum(len(r['variants']) for r in write_rows)),flush=True)
 for idx,row in enumerate(write_rows):
  result=client.graphql(UPDATE,row)['productVariantsBulkUpdate']
  if result['userErrors']:raise RuntimeError(str(result['userErrors']))
  if idx%20==0:print('Producten bijgewerkt: '+str(idx+1)+'/'+str(len(rows)),flush=True)
 old_by_id={v['id']:v for prod in products for v in prod['variants']['nodes']}
 def same_field(f):
  before={x['key']:x['value'] for x in old_by_id[f['ownerId']]['metafields']['nodes']}
  value=before.get(f['key'])
  if value==f['value']:return True
  if value is not None and f['type']=='number_decimal':
   try:return float(value)==float(f['value'])
   except ValueError:return False
  return False
 changed_fields=[f for f in fields if not same_field(f)]
 print('Metavelden bijwerken: '+str(len(changed_fields)),flush=True)
 for offset in range(0,len(changed_fields),25):
  r=client.graphql('''mutation($metafields:[MetafieldsSetInput!]!){metafieldsSet(metafields:$metafields){metafields{id} userErrors{field message}}}''',{'metafields':changed_fields[offset:offset+25]})['metafieldsSet']
  if r['userErrors']:raise RuntimeError(str(r['userErrors']))
 for offset in range(0,len(deletions),25):
  r=client.graphql('''mutation($metafields:[MetafieldIdentifierInput!]!){metafieldsDelete(metafields:$metafields){deletedMetafields{key} userErrors{field message}}}''',{'metafields':deletions[offset:offset+25]})['metafieldsDelete']
  if r['userErrors']:raise RuntimeError(str(r['userErrors']))
 after=snapshot(client);(run/'shopify-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2))
 actual={v['id']:v for p in after for v in p['variants']['nodes']};errors=[]
 for row in rows:
  for v in row['variants']:
   a=actual[v['id']]
   if float(a['price'])!=float(v['price']):errors.append((v['id'],'price'))
   weight=a['inventoryItem']['measurement']['weight']
   if not weight or weight['unit']!='KILOGRAMS' or abs(weight['value']-v['inventoryItem']['measurement']['weight']['value'])>1e-6:errors.append((v['id'],'weight'))
   if v['inventoryItem'].get('cost') and float(a['inventoryItem']['unitCost']['amount'])!=float(v['inventoryItem']['cost']):errors.append((v['id'],'cost'))
 for f in fields:
  a={x['key']:x['value'] for x in actual[f['ownerId']]['metafields']['nodes']}
  if a.get(f['key'])!=f['value']:
   if f['type']=='number_decimal' and a.get(f['key']) and float(a[f['key']])==float(f['value']):continue
   errors.append((f['ownerId'],f['key']))
 (run/'verification.json').write_text(json.dumps({'updated':len(source),'errors':errors,'missing':missing},ensure_ascii=False,indent=2))
 if errors:raise RuntimeError(str(errors[:20]))
 print(json.dumps({'verified':len(source),'errors':0,'run':str(run)}),flush=True)
if __name__=='__main__':main()
