"""Resolve unmatched portal family names by exact supplier SKU search."""
import asyncio,json,re,sqlite3,sys
from pathlib import Path
from urllib.parse import urlencode,quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.collect_certilas_order_units import parse_order_units
from app.suppliers.hub import supplier_database_path
from app.suppliers.certilas_portal import CDP_URL,portal_product_name
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
AUDIT=Path(__file__).resolve().parents[1]/'data/audit'
async def main():
 saved=json.loads((AUDIT/'certilas-order-units-20261009.json').read_text())
 output=AUDIT/'certilas-order-unit-fallbacks-20261009.json'
 state=json.loads(output.read_text()) if output.exists() else {'units':{},'families':{},'errors':{}}
 with sqlite3.connect(supplier_database_path('certilas')) as c:
  families={k:json.loads(data) for k,data in c.execute('select family_key,family_json from product_families')}
 async with async_playwright() as p:
  b=await p.chromium.connect_over_cdp(CDP_URL);ctx=b.contexts[0]
  async def get(url):
   await asyncio.sleep(5)
   for attempt in range(4):
    r=await ctx.request.get(url,timeout=30000);html=await r.text()
    if 'Too many requests' not in html:return r.url,html
    print('Portalbegrenzing: 60 seconden pauze',flush=True);await asyncio.sleep(60)
   raise ValueError('Portal rate limit')
  for key in saved['errors']:
   if key in state['families']:continue
   htmlpath=AUDIT/'certilas-order-unit-pages'/(re.sub(r'[^a-zA-Z0-9-]','_',key)+'.html')
   if not htmlpath.exists():continue
   try:
    f=families[key];skus=[str(v['sku']) for v in f.get('variants',[]) if str(v['sku']) not in saved['units']]
    if not skus:continue
    sku=skus[0]
    url,html=await get('https://portaal.certilas.nl/?'+urlencode(dict(pageid=30,alstart=0,artsearch=sku,artsearchmode='FUZZYOR'),quote_via=quote))
    anchors=BeautifulSoup(html,'html.parser').select('tr.product a[href]')
    normalize=lambda x:re.sub(r'[^a-z0-9]','',x.casefold())
    anchors.sort(key=lambda a:normalize(a.get_text())!=normalize(portal_product_name(f)))
    links=list(dict.fromkeys(a.get('href') for a in anchors))
    units=[]
    for link in links[:4]:
     url,html=await get(link.replace(' ','%20'));rows=parse_order_units(html,url)
     if any(r['sku']==sku for r in rows):units=rows;break
    if not units:raise ValueError('Geen exacte SKU-match '+sku)
    for row in units:state['units'][row['sku']]=row
    state['families'][key]={'url':url,'skus':[r['sku'] for r in units]}
    state['errors'].pop(key,None)
   except Exception as e:state['errors'][key]=str(e)
   output.write_text(json.dumps(state,ensure_ascii=False,indent=2));print(json.dumps({'families':len(state['families']),'units':len(state['units']),'errors':len(state['errors'])}),flush=True)
if __name__=='__main__':asyncio.run(main())
