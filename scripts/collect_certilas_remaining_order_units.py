"""Check remaining live variants by exact SKU, then validate each returned row."""
import asyncio,json,sys,re
from pathlib import Path
from urllib.parse import urlencode,quote
from difflib import SequenceMatcher
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.collect_certilas_order_units import parse_order_units
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
AUDIT=Path(__file__).resolve().parents[1]/'data/audit'
async def main():
 plan=json.loads((AUDIT/'certilas-minimum-order-20261009T211946Z/plan.json').read_text())
 missing=[r for r in plan['missing'] if r['reason']=='Geen gecontroleerde portalregel']
 output=AUDIT/'certilas-order-unit-final-20261009.json'
 state=json.loads(output.read_text()) if output.exists() else {'units':{},'errors':{}}
 async with async_playwright() as p:
  b=await p.chromium.connect_over_cdp('http://127.0.0.1:9222');ctx=b.contexts[0]
  async def get(url):
   await asyncio.sleep(3)
   for _ in range(4):
    r=await ctx.request.get(url,timeout=30000);html=await r.text()
    if 'Too many requests' not in html:return r.url,html
    await asyncio.sleep(60)
   raise ValueError('Portalbegrenzing')
  for row in missing:
   sku=row['sku']
   if sku in state['units']:continue
   try:
    url,html=await get('https://portaal.certilas.nl/?'+urlencode(dict(pageid=30,alstart=0,artsearch=sku,artsearchmode='FUZZYOR'),quote_via=quote))
    anchors=[a for a in BeautifulSoup(html,'html.parser').select('tr.product a[href]') if a.get_text(strip=True)]
    # Shopify titles retain the product name; compare words and compact names.
    normalize=lambda x:re.sub(r'[^a-z0-9]','',x.casefold())
    title=normalize(row['title'])
    anchors.sort(key=lambda a:(normalize(a.get_text()) not in title,-SequenceMatcher(None,normalize(a.get_text()),title).ratio()))
    links=list(dict.fromkeys(a.get('href') for a in anchors))
    units=[]
    for link in links[:3]:
     url,html=await get(link.replace(' ','%20'));rows=parse_order_units(html,url)
     if any(r['sku']==sku for r in rows):units=rows;break
    if not units:raise ValueError('Artikel ontbreekt in gecontroleerde bestelregels; '+url)
    for r in units:state['units'][r['sku']]=r
    state['errors'].pop(sku,None)
   except Exception as e:state['errors'][sku]=str(e)
   output.write_text(json.dumps(state,ensure_ascii=False,indent=2));print(json.dumps({'sku':sku,'units':len(state['units']),'errors':len(state['errors'])}),flush=True)
if __name__=='__main__':asyncio.run(main())
