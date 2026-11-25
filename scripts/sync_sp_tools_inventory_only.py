#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, sys, time
from pathlib import Path

PROJECT=Path('/root/weldingshop-pim')
sys.path.insert(0,str(PROJECT))
from app.suppliers.hub import get_supplier, read_source
from app.shopify.client import ShopifyClient
from app.shopify.sync import _shopify_products

LOCATION_ID='gid://shopify/Location/116493091145'
LOCATION_NAME='Magazijn-SP-Tools'
TARGET_AVAILABLE=5
OUT_DIR=PROJECT/'data/output/shopify/sp-tools-inventory-only'
CURRENT_QUERY='''query CurrentInventory($ids:[ID!]!,$locationId:ID!){nodes(ids:$ids){... on InventoryItem{id sku level:inventoryLevel(locationId:$locationId){quantities(names:["available"]){name quantity}}}}}'''
ACTIVATE_MUTATION='''mutation ActivateInventory($inventoryItemId:ID!,$locationId:ID!,$available:Int,$idempotencyKey:String!){inventoryActivate(inventoryItemId:$inventoryItemId,locationId:$locationId,available:$available) @idempotent(key:$idempotencyKey){inventoryLevel{id quantities(names:["available"]){name quantity}} userErrors{field message}}}'''
SET_MUTATION='''mutation SetInventory($input:InventorySetQuantitiesInput!,$idempotencyKey:String!){inventorySetQuantities(input:$input) @idempotent(key:$idempotencyKey){userErrors{field message code} inventoryAdjustmentGroup{createdAt reason changes{name delta item{id}}}}}'''

def chunks(xs,n):
 for i in range(0,len(xs),n): yield xs[i:i+n]
def qty(level):
 for q in (level or {}).get('quantities') or []:
  if q.get('name')=='available': return int(q.get('quantity') or 0)
 return 0
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def compact_existing(row):
 p=row['product'];v=row['variant'];return {'sku':str(v.get('sku') or '').strip().upper(),'productId':p['id'],'variantId':v['id'],'inventoryItemId':v['inventoryItem']['id'],'status':p.get('status'),'handle':p.get('handle'),'title':p.get('title')}
def current_levels(client,items):
 out={}
 for group in chunks(items,100):
  data=client.graphql(CURRENT_QUERY,{'ids':[x['inventoryItemId'] for x in group],'locationId':LOCATION_ID})
  for node in data.get('nodes') or []:
   if node: out[node['id']]={'exists':bool(node.get('level')),'quantity':qty(node.get('level')),'sku':node.get('sku')}
 return out
def idem(prefix,items):
 raw='|'.join(sorted(f"{x['inventoryItemId']}:{x['target']}:{x.get('current')}" for x in items))
 return f"sp-tools-{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
def main():
 ap=argparse.ArgumentParser(description='SP Tools inventory-only sync; no product/content/status/price/metafield writes')
 ap.add_argument('--apply',action='store_true');ap.add_argument('--report');args=ap.parse_args()
 started=now();supplier=get_supplier('sp-tools',include_credentials=True)
 if not supplier: raise RuntimeError('SP Tools supplier config ontbreekt')
 analysis=read_source(supplier)
 live={}
 invalid=[]
 for r in analysis.records:
  sku=str(r.get('reference') or '').strip().upper();stock=str(r.get('stock') or '').strip()
  if not sku: continue
  if stock not in {'0','1'}: invalid.append({'sku':sku,'stock':stock});continue
  live[sku]=TARGET_AVAILABLE if stock=='1' else 0
 if invalid: raise RuntimeError(f'Onbekende stockwaarden: {invalid[:10]}')
 if len(live)<2500: raise RuntimeError(f'Brondekking te laag: {len(live)}')
 client=ShopifyClient.from_settings();raw=_shopify_products(client,'SP Tools','sp-tools')
 existing={k.upper():compact_existing(v) for k,v in raw.items() if v.get('variant',{}).get('inventoryItem',{}).get('id')}
 common=sorted(set(live)&set(existing));missing_from_feed=sorted(set(existing)-set(live));source_only=sorted(set(live)-set(existing))
 if len(common)<int(len(existing)*0.90): raise RuntimeError(f'Gemeenschappelijke dekking te laag: {len(common)}/{len(existing)}')
 current=current_levels(client,[existing[k] for k in common]);changes=[];unchanged=0
 for sku in common:
  item=existing[sku];state=current.get(item['inventoryItemId'],{'exists':False,'quantity':0});target=live[sku]
  row={**item,'current':state['quantity'],'target':target,'locationExists':state['exists']}
  if state['quantity']!=target or not state['exists']: changes.append(row)
  else: unchanged+=1
 if len(changes)>max(700,int(len(common)*0.25)): raise RuntimeError(f'Wijzigingsomvang onverwacht groot: {len(changes)}/{len(common)}')
 report={'startedAt':started,'mode':'apply' if args.apply else 'dry-run','source':{'format':analysis.format,'records':len(live),'available':sum(v>0 for v in live.values()),'unavailable':sum(v==0 for v in live.values())},'shopify':{'existingSkus':len(existing),'common':len(common),'missingFromFeed':len(missing_from_feed),'sourceOnly':len(source_only),'unchanged':unchanged,'changesPlanned':len(changes),'location':LOCATION_NAME,'locationId':LOCATION_ID},'guarantees':{'writesOnly':['inventoryActivate when level absent','inventorySetQuantities available at Magazijn-SP-Tools'],'neverWrites':['Weldingshop inventory','other locations','product status','inventory policy','price','cost','title','description','tags','collections','publication','media','metafields','products or variants']},'samples':{'changes':changes[:40],'missingFromFeed':missing_from_feed[:40],'sourceOnly':source_only[:40]},'writes':{'activated':0,'set':0,'errors':[]},'verification':None}
 OUT_DIR.mkdir(parents=True,exist_ok=True);report_path=Path(args.report) if args.report else OUT_DIR/(dt.datetime.now().strftime('%Y%m%dT%H%M%SZ')+('-apply.json' if args.apply else '-dry-run.json'))
 report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 if not args.apply:
  print(json.dumps({'status':'dry-run','report':str(report_path),'source':report['source'],'shopify':report['shopify'],'samples':report['samples']},ensure_ascii=False,indent=2));return
 for row in [x for x in changes if not x['locationExists']]:
  data=client.graphql(ACTIVATE_MUTATION,{'inventoryItemId':row['inventoryItemId'],'locationId':LOCATION_ID,'available':row['target'],'idempotencyKey':idem('activate',[row])})
  errs=(data.get('inventoryActivate') or {}).get('userErrors') or []
  if errs: report['writes']['errors'].append({'sku':row['sku'],'stage':'activate','errors':errs})
  else: report['writes']['activated']+=1
 if report['writes']['errors']: raise RuntimeError('Activatiefouten; setfase niet gestart')
 pending=[x for x in changes if x['locationExists']]
 for group in chunks(pending,50):
  quantities=[{'inventoryItemId':x['inventoryItemId'],'locationId':LOCATION_ID,'quantity':x['target'],'changeFromQuantity':x['current']} for x in group]
  data=client.graphql(SET_MUTATION,{'input':{'name':'available','reason':'correction','quantities':quantities},'idempotencyKey':idem('set',group)})
  errs=(data.get('inventorySetQuantities') or {}).get('userErrors') or []
  if errs: report['writes']['errors'].append({'skus':[x['sku'] for x in group],'stage':'set','errors':errs})
  else: report['writes']['set']+=len(group)
 if report['writes']['errors']: raise RuntimeError('Voorraadmutatiefouten')
 time.sleep(2);after=current_levels(client,[existing[k] for k in common]);mismatch=[]
 for sku in common:
  state=after.get(existing[sku]['inventoryItemId'],{'exists':False,'quantity':0})
  if not state['exists'] or state['quantity']!=live[sku]: mismatch.append({'sku':sku,'expected':live[sku],**state})
 report['verification']={'checked':len(common),'mismatches':len(mismatch),'sample':mismatch[:40]};report['finishedAt']=now();report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 if mismatch: raise RuntimeError(f'Readbackafwijkingen: {len(mismatch)}')
 print(json.dumps({'status':'applied','report':str(report_path),'source':report['source'],'shopify':report['shopify'],'writes':report['writes'],'verification':report['verification']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
