"""Read Certilas portal order units without adding products to a cart."""
from __future__ import annotations
import asyncio,json,re,sys,sqlite3,argparse
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.suppliers.hub import get_supplier,supplier_database_path
from app.suppliers.certilas_portal import PLAYWRIGHT_SITE_PACKAGES,CDP_URL,portal_product_name
sys.path.append(PLAYWRIGHT_SITE_PACKAGES)
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

def parse_order_units(html,url):
 soup=BeautifulSoup(html,'html.parser');result=[]
 for inp in soup.select('input[name^="txtCount_"]'):
  sku=inp['name'][9:];row=inp.find_parent('tr')
  match=re.search(r"changeWeightAmount\(this,\s*['\"]([0-9.,]+)['\"]\)",inp.get('onchange',''))
  if not row or not match: continue
  def number(v): return float(str(v).replace(',','.'))
  minimum=number(inp.get('min','0')); step=number(inp.get('step','0'));weight=number(match[1])
  ean=re.search(r'\b\d{13}\b',row.get_text(' ',strip=True))
  if not ean or min(minimum,step,weight)<=0:continue
  result.append(dict(sku=sku,ean=ean[0],minimum_quantity=minimum,quantity_step=step,
    kg_per_order_unit=weight,minimum_order_kg=round(weight*minimum,6),source_url=url,
    checked_at=datetime.now(timezone.utc).isoformat(),portal_row=row.get_text(' ',strip=True)))
 return result

async def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',default='/root/weldingshop-pim/data/audit/certilas-order-units-20261009.json');args=ap.parse_args()
 out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
 state=json.loads(out.read_text()) if out.exists() else {'families':{},'units':{},'errors':{}}
 with sqlite3.connect(supplier_database_path('certilas')) as c:
  families=[json.loads(r[0]) for r in c.execute('select family_json from product_families')]
 async with async_playwright() as p:
  browser=await p.chromium.connect_over_cdp(CDP_URL);context=browser.contexts[0]
  page=await context.new_page()
  try:
   await page.goto('https://portaal.certilas.nl/nl/',wait_until='domcontentloaded')
   if await page.locator('input[name="txtBIZ_UN"]').count():
    supplier=get_supplier('certilas',include_credentials=True)
    await page.locator('input[name="txtBIZ_UN"]').fill(supplier['dealer_username'])
    await page.locator('input[name="txtBIZ_PW"]').fill(supplier['dealer_secret'])
    await page.locator('button[type="submit"],input[type="submit"]').first.click()
    await page.wait_for_load_state('domcontentloaded')
   sem=asyncio.Semaphore(1)
   failures=out.parent / 'certilas-order-unit-pages'
   failures.mkdir(exist_ok=True)
   site=json.loads((out.parent / 'certilas-shopify-before-order-units-20261009.json').read_text())
   site_skus={v['sku'] for v in site['variants']}
   families.sort(key=lambda f: not any(v.get('sku') in site_skus for v in f.get('variants',[])))
   async def collect(f):
    key=f['family_key']
    if key in state['families']:return
    url='https://portaal.certilas.nl/default.asp?'+urlencode(dict(pageid=29,artdetail=portal_product_name(f),webgroupfilter=1),quote_via=quote)
    async with sem:
     try:
      await asyncio.sleep(3)
      for attempt in range(4):
       r=await context.request.get(url,timeout=30000);html=await r.text()
       if 'Too many requests' not in html:break
       print('Portal rate limit; pause 60 seconds',flush=True)
       await asyncio.sleep(60)
      if 'Too many requests' in html:raise RuntimeError('Portal rate limit')
      units=parse_order_units(html,r.url)
      if not units:
       (failures / (re.sub(r'[^a-zA-Z0-9-]','_',key)+'.html')).write_text(html)
       raise ValueError('Geen bestelregels gevonden: '+r.url)
      for unit in units:
       prev=state['units'].get(unit['sku'])
       if prev and any(prev[k]!=unit[k] for k in ['ean','minimum_quantity','quantity_step','kg_per_order_unit']):
        raise ValueError('Tegenstrijdige bestelregels '+unit['sku'])
       state['units'][unit['sku']]=unit
      state['families'][key]=dict(url=r.url,skus=[u['sku'] for u in units])
      state['errors'].pop(key,None)
     except Exception as e:state['errors'][key]=str(e)
     out.write_text(json.dumps(state,ensure_ascii=False,indent=2))
     if (len(state['families'])+len(state['errors']))%25==0:print(json.dumps(dict(families=len(state['families']),units=len(state['units']),errors=len(state['errors']))),flush=True)
   await asyncio.gather(*(collect(f) for f in families))
   print(json.dumps(dict(families=len(state['families']),total_families=len(families),units=len(state['units']),errors=len(state['errors']))),flush=True)
  finally:await page.close()
if __name__=='__main__':asyncio.run(main())
