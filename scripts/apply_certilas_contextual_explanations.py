#!/usr/bin/env python3
"""Maak uitlegblogs en voeg uitsluitend relevante links aan Certilas-PIM-teksten toe."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from app.product_families import rebuild_product_families
from app.shopify.client import ShopifyClient
from app.suppliers.hub import supplier_database_path, utc_now
from scripts.create_welding_knowledge_blogs import ASSET_DIR, BLOG_HANDLE, blog_id, upload_file


NEW_BLOGS = [
    {"handle":"hardoplassen-hardheid-en-slijtage","title":"Hardoplassen, hardheid en slijtage praktisch uitgelegd","image":"hardoplassen-hardheid-slijtage.png","alt":"Cartoon van hardoplassen op een slijtagedeel","tags":["Hardoplassen","Hardheid","Slijtage"],"summary":"Het verschil tussen hardheid, slijtvastheid en taaiheid en hoe u een hardoplaslegering kiest.","body":"""
<h2>Hard is niet automatisch slijtvast</h2><p>Hardoplassen brengt een slijtvaste laag aan op een nieuw of versleten onderdeel. De beste keuze hangt niet alleen af van de hardheid, maar ook van het soort slijtage: metaal-op-metaal, schurende mineralen, slagbelasting, druk of hoge temperatuur.</p>
<h2>HRC en HB</h2><p>HRC is de Rockwell C-schaal en wordt veel gebruikt voor harde laslagen. HB is de Brinell-schaal en komt vaak voor bij zachtere of koudverstevigende materialen. Waarden uit verschillende meetschalen mogen niet rechtstreeks als hetzelfde getal worden vergeleken.</p>
<h2>Hardheid, taaiheid en scheurtjes</h2><p>Een zeer harde laag kan minder goed tegen zware slagen. Bij sommige hooggelegeerde hardoplaslagen zijn fijne dwarsscheurtjes normaal en helpen ze spanning af te bouwen. Dat is iets anders dan loslaten of scheuren in het basismateriaal.</p>
<h2>Bufferlaag en aantal lagen</h2><p>Een taaie bufferlaag kan nodig zijn tussen het basismateriaal en de harde deklaag. Houd rekening met verdunning: vooral de eerste laag mengt met het basismateriaal en bereikt daardoor niet altijd direct de opgegeven eindhardheid.</p>
<h2>Praktische keuze</h2><ul><li>Bepaal het dominante slijtagemechanisme.</li><li>Controleer slagbelasting en bedrijfstemperatuur.</li><li>Volg advies over voorwarmen, laagdikte en aantal lagen.</li><li>Controleer of verspanen mogelijk is of alleen slijpen.</li></ul>"""},
    {"handle":"beschermgassen-voor-mig-mag-en-tig","title":"Beschermgassen voor MIG/MAG en TIG uitgelegd","image":"beschermgassen-mig-mag-tig.png","alt":"Cartoon over beschermgas bij MIG MAG en TIG lassen","tags":["Beschermgas","MIG/MAG","TIG"],"summary":"Wat aanduidingen zoals I1, M21 en C1 betekenen en waarom het gas bij de lasdraad moet passen.","body":"""
<h2>Waarom beschermgas nodig is</h2><p>Beschermgas houdt zuurstof, stikstof en vocht uit het vloeibare lasbad. Het gekozen gas beïnvloedt booggedrag, inbranding, lassnelheid, spatten, oxidatie en de uiteindelijke mechanische eigenschappen.</p>
<h2>Veelgebruikte gasgroepen</h2><ul><li><strong>I1:</strong> argon, veel gebruikt bij TIG en MIG-lassen van onder meer aluminium en RVS.</li><li><strong>M21:</strong> argonrijk menggas met actieve bestanddelen, veel gebruikt voor MAG-lassen van staal.</li><li><strong>C1:</strong> koolstofdioxide, actief gas met diepe inbranding maar doorgaans meer spatten.</li></ul>
<h2>MIG, MAG en TIG</h2><p>Bij MIG is het gas inert; bij MAG bevat het gas actieve componenten. TIG gebruikt meestal een inert gas. Een kleine wijziging in gassamenstelling kan de classificatie of eigenschappen van het lasmetaal veranderen.</p>
<h2>Gebruik het voorgeschreven gas</h2><p>Neem de gasgroep uit het productdatablad of de WPS over. Controleer daarnaast gasflow, lekkage, tocht, afstand van de gascup en vervuiling. Meer gasflow geeft niet automatisch betere bescherming en kan juist lucht aanzuigen.</p>"""},
    {"handle":"waterstofarm-lassen-h5-h10","title":"Waterstofarm lassen en H5/H10 uitgelegd","image":"waterstofarm-lassen-h5-h10.png","alt":"Cartoon over droog bewaren van laselektroden en waterstofscheuren","tags":["Waterstofarm","H5","H10","Elektroden"],"summary":"Wat diffusibele waterstof en de klassen H5 en H10 betekenen en waarom droog bewaren belangrijk is.","body":"""
<h2>Waarom waterstof aandacht vraagt</h2><p>Diffusibele waterstof kan samen met hoge spanning en een gevoelig materiaal vertraagde koudscheuren veroorzaken. Deze kunnen pas uren na het lassen zichtbaar worden.</p>
<h2>Betekenis van H5 en H10</h2><p>De H-aanduiding is een klasse voor het maximale gehalte diffusibele waterstof in het neergesmolten lasmetaal onder vastgelegde proefcondities. H5 stelt een strengere grens dan H10. Het is geen garantie dat een vochtige of verkeerd behandelde elektrode in de praktijk dezelfde waarde behoudt.</p>
<h2>Droog bewaren en herdrogen</h2><p>Basische elektroden en sommige poeders nemen vocht op. Bewaar en behandel ze volgens de aanwijzingen van de fabrikant. Temperatuur en tijd voor herdrogen verschillen per product; gebruik daarvoor nooit een algemene gokwaarde.</p>
<h2>Meer maatregelen</h2><ul><li>Maak de lasnaad schoon en droog.</li><li>Pas voorgeschreven voorwarm- en interpasstemperaturen toe.</li><li>Beperk opsluiting en restspanningen waar mogelijk.</li><li>Volg bij kritisch werk altijd de WPS.</li></ul>"""},
    {"handle":"ongelijksoortige-metalen-lassen","title":"Ongelijksoortige metalen lassen praktisch uitgelegd","image":"ongelijksoortige-metalen-lassen.png","alt":"Cartoon van een lasverbinding tussen koolstofstaal en roestvast staal","tags":["Ongelijksoortige metalen","RVS","Bufferlaag"],"summary":"Waar u op let bij verbindingen tussen verschillende staalsoorten en bij het aanbrengen van bufferlagen.","body":"""
<h2>Twee materialen, één lasmetaal</h2><p>Bij ongelijksoortige verbindingen mengen beide basismaterialen met het toevoegmateriaal. Daardoor kan de samenstelling van de eerste laslaag sterk afwijken van die van de draad of elektrode.</p>
<h2>Verdunning en scheurgevoeligheid</h2><p>De keuze moet rekening houden met verdunning, uitzettingsverschil, hardingsneiging, corrosie, bedrijfstemperatuur en mechanische belasting. Een toevoegmateriaal dat afzonderlijk bij beide materialen past, is niet automatisch geschikt voor de combinatie.</p>
<h2>Bufferlaag</h2><p>Een bufferlaag vormt een taaie of metallurgisch geschikte overgang voordat de uiteindelijke deklaag of verbinding wordt gelast. Dit wordt onder meer toegepast bij RVS aan staal, moeilijk lasbare staalsoorten en reparatie- of hardoplaswerk.</p>
<h2>Veilige werkwijze</h2><ul><li>Identificeer beide basismaterialen.</li><li>Controleer bedrijfs- en corrosiecondities.</li><li>Beperk ongewenste vermenging met de juiste techniek.</li><li>Gebruik voor kritisch werk een gekwalificeerde lasmethode en WPS.</li></ul>"""},
    {"handle":"voorwarmen-interpass-en-warmtebehandeling","title":"Voorwarmen, interpasstemperatuur en warmtebehandeling uitgelegd","image":"voorwarmen-interpass-warmtebehandeling.png","alt":"Cartoon van temperatuurcontrole bij een voorverwarmde lasverbinding","tags":["Voorwarmen","Interpass","Warmtebehandeling"],"summary":"Waarom temperatuur vóór, tijdens en na het lassen bepalend kan zijn voor een betrouwbare verbinding.","body":"""
<h2>Voorwarmen</h2><p>Voorwarmen vertraagt de afkoeling, helpt vocht te verwijderen en kan de kans op harde, scheurgevoelige zones verminderen. De vereiste temperatuur hangt onder meer af van materiaal, koolstofequivalent, plaatdikte, warmte-inbreng en waterstofniveau.</p>
<h2>Interpasstemperatuur</h2><p>De interpasstemperatuur is de temperatuur van het werkstuk voordat de volgende laag wordt gelegd. Zowel een te lage als een te hoge waarde kan ongewenst zijn. Meet op de voorgeschreven plaats en met een geschikt meetmiddel.</p>
<h2>Warmtebehandeling na het lassen</h2><p>Een warmtebehandeling na het lassen kan spanningen verminderen of de gewenste materiaalstructuur herstellen. Temperatuur, opwarmsnelheid, houdtijd en afkoeling moeten bij materiaal en procedure passen.</p>
<h2>Hoge en lage bedrijfstemperatuur</h2><p>Kruipvaste materialen zijn bedoeld voor langdurige belasting bij verhoogde temperatuur. Voor lage temperaturen is vooral voldoende kerftaaiheid bij de ontwerptemperatuur belangrijk. Kies niet uitsluitend op treksterkte of handelsnaam.</p>
<p>Volg altijd het productdatablad, de materiaalnorm en bij gekwalificeerd werk de WPS; algemene richtwaarden vervangen geen lasprocedure.</p>"""},
]


RULES = [
    ("Classificaties en materiaalnummers", "/blogs/news/lasnormeringen-en-classificaties-uitgelegd", r"classific|en\s*iso|aws\s*:|din\s*:|w[_ .-]?nr|werkstoff"),
    ("Lasposities", "/blogs/news/lasposities-pa-pg-uitgelegd", r"laspositie|welding position"),
    ("PREN en putcorrosie", "/blogs/news/putcorrosie-en-pren-bij-rvs", r"\bpren\b|putcorros|pitting"),
    ("Mechanische eigenschappen", "/blogs/news/natteigenschappen-en-mechanische-eigenschappen-lasmetaal", r"natteig|wetting|treksterkte|vloeigrens|rekgrens|kerftaai|slagwaarde|cryog|lage temperatuur"),
    ("Hardoplassen en slijtage", "/blogs/news/hardoplassen-hardheid-en-slijtage", r"hardoplas|hardfacing|slijtage|hardheid|\bhrc\b|\bhb\b|koudverstevig"),
    ("Beschermgassen", "/blogs/news/beschermgassen-voor-mig-mag-en-tig", r"beschermgas|gasbescherm|\bm21\b|\bm12\b|\bc1\b|\bi1\b|argon|koolstofdioxide"),
    ("Waterstofarm lassen", "/blogs/news/waterstofarm-lassen-h5-h10", r"waterstof|\bh5\b|\bh10\b|herdrogen|droogkast"),
    ("Ongelijksoortige metalen", "/blogs/news/ongelijksoortige-metalen-lassen", r"ongelijksoort|dissimilar|bufferlaag|zwart.?wit|rvs.{0,20}staal|staal.{0,20}rvs"),
    ("Voorwarmen en warmtebehandeling", "/blogs/news/voorwarmen-interpass-en-warmtebehandeling", r"voorwarm|interpas|warmtebehandel|zachtgegl|spanningsarm|kruip|hittebest|hoge temperatuur"),
]


def enrich(value: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(value or "", "html.parser")
    for old in soup.select("div.product-uitleg"):
        old.decompose()
    source = soup.get_text(" ", strip=True).casefold()
    matches = [(label, url) for label, url, pattern in RULES if re.search(pattern, source, re.I)]
    if not matches:
        return str(soup), []
    block = soup.new_tag("div"); block["class"] = "product-uitleg"
    heading = soup.new_tag("h3"); heading.string = "Technische uitleg"; block.append(heading)
    listing = soup.new_tag("ul")
    for label, url in matches:
        li = soup.new_tag("li"); li.append(f"{label} (")
        link = soup.new_tag("a", href=url, target="_blank", rel="noopener noreferrer")
        link.string = "Uitleg"; li.append(link); li.append(")"); listing.append(li)
    block.append(listing); soup.append(block)
    return str(soup), [label for label, _ in matches]


def upsert_blogs(client: ShopifyClient) -> list[dict]:
    target = blog_id(client)
    nodes = client.graphql("query($id:ID!){blog(id:$id){articles(first:250){nodes{id handle}}}}", {"id":target})["blog"]["articles"]["nodes"]
    existing = {item["handle"]: item["id"] for item in nodes}
    results = []
    for item in NEW_BLOGS:
        article = {"title":item["title"],"handle":item["handle"],"author":{"name":"Admin"},"body":item["body"],"summary":item["summary"],"tags":item["tags"],"isPublished":False}
        if item["handle"] in existing:
            query = "mutation($id:ID!,$article:ArticleUpdateInput!){articleUpdate(id:$id,article:$article){article{id title handle isPublished} userErrors{field message}}}"
            payload = client.graphql(query,{"id":existing[item["handle"]],"article":article})["articleUpdate"]
        else:
            image_url = upload_file(client, ASSET_DIR/item["image"], item["alt"])
            article["blogId"] = target; article["image"] = {"url":image_url,"altText":item["alt"]}
            query = "mutation($article:ArticleCreateInput!){articleCreate(article:$article){article{id title handle isPublished} userErrors{field message}}}"
            payload = client.graphql(query,{"article":article})["articleCreate"]
        if payload["userErrors"]: raise RuntimeError(str(payload["userErrors"]))
        results.append(payload["article"])
    return results


def main() -> dict:
    path = supplier_database_path("certilas")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}-before-contextual-explanations-{stamp}.sqlite")
    counts: dict[str,int] = {}
    changed = 0
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target: source.backup(target)
    with sqlite3.connect(path) as connection:
        rows = connection.execute("select sku,html_description from products where coalesce(html_description,'')<>''").fetchall()
        for sku, value in rows:
            updated, labels = enrich(value)
            if updated != value:
                connection.execute("update products set html_description=?,updated_at=? where sku=?",(updated,utc_now(),sku)); changed += 1
            for label in labels: counts[label] = counts.get(label,0)+1
        integrity = connection.execute("pragma integrity_check").fetchone()[0]
    rebuild_product_families("certilas", path)
    blogs = upsert_blogs(ShopifyClient.from_settings())
    return {"updated_products":changed,"topic_counts":counts,"new_blogs":blogs,"backup":str(backup),"integrity":integrity}


if __name__ == "__main__": print(json.dumps(main(),ensure_ascii=False))
